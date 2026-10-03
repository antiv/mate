"""
Run an agent from a stored config version on the LangGraph runtime, for evals.

The counterpart of the ADK SnapshotAgent in shared/utils/eval_agent_runner.py:
the root agent is built from the version's snapshot and its sub-agents from
their current config. The graph is built outside the builder's cache and runs
against an in-memory checkpointer. Events are not written to the LangGraph
session tables, and artifacts the tools save go to an in-memory artifact
service, as on ADK. The deployed graph is untouched.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from shared.utils.eval_agent_runner import (_EVAL_RUN, DEFAULT_TIMEOUT, EVAL_USER_ID,
                                            ReplyCollector)

logger = logging.getLogger(__name__)


class LangGraphSnapshotAgent:
    """
    One agent built from a snapshot, asked any number of questions, each in a
    fresh thread. Build once per suite: MCP tools start with the graph.

        async with LangGraphSnapshotAgent(snapshot) as agent:
            reply = await agent.ask("What are your opening hours?")
    """

    def __init__(self, snapshot: Dict[str, Any], builder: Optional[Any] = None):
        if not snapshot or not snapshot.get("name"):
            raise ValueError("The version has no usable config snapshot")
        self.snapshot = snapshot
        self._builder = builder
        self._built = None
        self._artifacts = None

    async def __aenter__(self) -> "LangGraphSnapshotAgent":
        from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
        from langgraph.checkpoint.memory import InMemorySaver
        from shared.utils.langgraph.agent_builder import AgentBuilder
        from shared.utils.langgraph.artifact_adapter import ArtifactAdapter

        # A fresh builder: build_for_config does not cache, and the shared
        # builder's cache of deployed graphs is never touched.
        builder = self._builder or AgentBuilder()
        self._built = await builder.build_for_config(dict(self.snapshot), InMemorySaver())
        self._artifacts = ArtifactAdapter(InMemoryArtifactService())
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def ask(self, input_text: str, timeout: float = DEFAULT_TIMEOUT,
                  events: Optional[List[Dict[str, Any]]] = None) -> str:
        """The agent's reply. Pass a list as *events* to also collect the turn's events."""
        from shared.utils.langgraph.event_translator import translate_stream
        from shared.utils.langgraph.executor import _apply_output_guardrails, _log_token_usage
        from shared.utils.langgraph.hooks import check_input_guardrails
        from shared.utils.langgraph.tool_adapter import (RunContext, reset_run_context,
                                                         set_run_context)

        built = self._built
        session_id = f"eval-{uuid.uuid4()}"
        invocation_id = f"e-{uuid.uuid4()}"
        meta = {"request_id": invocation_id, "session_id": session_id, "user_id": EVAL_USER_ID}
        collector = ReplyCollector()

        block_message = check_input_guardrails(
            built.guardrail_engines.get(built.name), input_text, built.name, meta)
        if block_message:
            return block_message

        config = {"configurable": {"thread_id": session_id, "user_id": EVAL_USER_ID,
                                   "app_name": built.name}}
        graph_input = {"messages": [HumanMessage(content=meta.get("redacted_text") or input_text)]}

        async def _run() -> None:
            stream = built.graph.astream(graph_input, config=config,
                                         stream_mode=["messages", "updates"], subgraphs=True)
            async for event, is_complete in translate_stream(
                    stream, author=built.name, invocation_id=invocation_id):
                if not is_complete:
                    continue
                _apply_output_guardrails(event, built.guardrail_engines, meta)
                _log_token_usage(event, built.name, EVAL_USER_ID, session_id, built.model_names,
                                 built.fallback_models)
                collector.feed(event)
                if events is not None:
                    events.append(event)

        run_context = RunContext(app_name=built.name, user_id=EVAL_USER_ID,
                                 session_id=session_id, agent_name=built.name,
                                 artifact_adapter=self._artifacts)
        context_token = set_run_context(run_context)
        eval_token = _EVAL_RUN.set(True)
        try:
            await asyncio.wait_for(_run(), timeout=timeout)
        finally:
            _EVAL_RUN.reset(eval_token)
            reset_run_context(context_token)
        return collector.text
