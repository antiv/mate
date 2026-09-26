"""
Run an agent from a stored config version, in memory, for evals.

Evals used to call the live runtime by agent name, so "score against version N"
scored whatever was deployed and filed the result under N. Here the agent is built
from the version's snapshot instead and run through an ADK Runner with an
in-memory session, the way ephemeral subagents run. Nothing is written to
agents_config and the deployed agent is untouched.

Only the agent the version belongs to comes from the snapshot. Its sub-agents are
built from their current config: they are separate rows with versions of their
own, and this is also what checking a proposed change before applying it needs.
"""

import asyncio
import contextvars
import logging
import os
from typing import Any, Dict, Optional

from google.genai import types

from .models import AgentConfig

logger = logging.getLogger(__name__)

EVAL_USER_ID = "eval_runner"
DEFAULT_TIMEOUT = 120.0

LANGGRAPH_UNSUPPORTED = (
    "Running evals against a stored version is not supported on the LangGraph runtime yet. "
    "It would otherwise run the deployed agent and file the result under the selected version."
)


# Set only while SnapshotAgent.ask runs. Evals are started by an admin, so RBAC,
# which would refuse the eval user on an admin-only agent, is skipped for them.
_EVAL_RUN = contextvars.ContextVar("mate_eval_run", default=False)


def is_eval_run() -> bool:
    return _EVAL_RUN.get()


class LangGraphNotSupported(RuntimeError):
    """Raised instead of silently falling back to the deployed agent."""


def langgraph_active() -> bool:
    return os.getenv("AGENT_FRAMEWORK", "adk").lower() == "langgraph"


class ReplyCollector:
    """
    The final user-facing reply from a stream of ADK events in their JSON form.

    Text is tracked per author and reset when the author changes or a tool call
    or response appears, so only the last author's closing text remains; routing
    events are ignored. A streamed partial and its completed event are
    de-duplicated. Accepts both the camelCase keys of the SSE wire format and
    the snake_case of Event.model_dump().
    """

    def __init__(self) -> None:
        self._author = ""
        self._text = ""

    def feed(self, event: Dict[str, Any]) -> None:
        actions = event.get("actions") or {}
        if actions.get("transfer_to_agent") or actions.get("escalate"):
            return

        author = event.get("author", "")
        if author != self._author:
            self._author = author
            self._text = ""

        parts = (event.get("content") or {}).get("parts") or []
        if any(p.get("functionCall") or p.get("functionResponse")
               or p.get("function_call") or p.get("function_response") for p in parts):
            self._text = ""
            return

        for part in parts:
            t = part.get("text")
            if not t:
                continue
            if self._text and t.startswith(self._text):
                self._text = t
            elif self._text and self._text.startswith(t):
                pass
            else:
                self._text += t

    @property
    def text(self) -> str:
        return self._text.strip()


def config_from_snapshot(snapshot: Dict[str, Any]) -> AgentConfig:
    """An unsaved AgentConfig carrying the snapshot's values."""
    columns = {c.name for c in AgentConfig.__table__.columns}
    return AgentConfig(**{k: v for k, v in snapshot.items() if k in columns})


class SnapshotAgent:
    """
    One agent built from a snapshot, asked any number of questions, each in a
    fresh session. Build once per suite: MCP toolsets start with the agent.

        async with SnapshotAgent(snapshot) as agent:
            reply = await agent.ask("What are your opening hours?")
    """

    def __init__(self, snapshot: Dict[str, Any], manager: Optional[Any] = None):
        if langgraph_active():
            raise LangGraphNotSupported(LANGGRAPH_UNSUPPORTED)
        if not snapshot or not snapshot.get("name"):
            raise ValueError("The version has no usable config snapshot")
        self.snapshot = snapshot
        self._manager = manager
        self._runner = None
        self._session_service = None
        self._app_name = None

    async def __aenter__(self) -> "SnapshotAgent":
        from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService
        from .agent_manager import AgentManager
        from .utils import create_app_with_context_caching

        manager = self._manager or AgentManager()
        agent = manager.build_tree_for_config(config_from_snapshot(self.snapshot))
        if agent is None:
            raise RuntimeError(manager.last_error or
                               f"Could not build agent '{self.snapshot['name']}' from its version")

        # The same App wrapper the runtime uses, so plugins and callbacks match
        app = create_app_with_context_caching(root_agent=agent, app_name=agent.name)
        self._app_name = agent.name
        self._session_service = InMemorySessionService()
        self._runner = Runner(app=app, session_service=self._session_service,
                              artifact_service=InMemoryArtifactService())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._runner is not None:
            try:
                await self._runner.close()
            except Exception as e:
                logger.warning("Closing eval runner for %s failed: %s", self._app_name, e)

    async def ask(self, input_text: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        session = await self._session_service.create_session(
            app_name=self._app_name, user_id=EVAL_USER_ID)
        message = types.Content(role="user", parts=[types.Part.from_text(text=input_text)])
        collector = ReplyCollector()

        async def _run() -> None:
            async for event in self._runner.run_async(
                    user_id=EVAL_USER_ID, session_id=session.id, new_message=message):
                collector.feed(event.model_dump(mode="json", exclude_none=True))

        token = _EVAL_RUN.set(True)
        try:
            await asyncio.wait_for(_run(), timeout=timeout)
        finally:
            _EVAL_RUN.reset(token)
        return collector.text
