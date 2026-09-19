"""
Executes one /run_sse invocation against the compiled LangGraph agent graph and
yields complete/partial events in ADK wire shape.

Order of operations per run: RBAC check → input guardrails → (HITL resume or
new message) → graph stream → event translation with output guardrails, state/
artifact delta flushing, event persistence and token logging.
"""

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain_core.messages import HumanMessage

from shared.utils.langgraph.agent_builder import (
    AgentNotFoundError,
    UnsupportedAgentTypeError,
    get_agent_builder,
)
from shared.utils.langgraph.event_translator import translate_stream
from shared.utils.langgraph.session_store import get_session_store

logger = logging.getLogger(__name__)


def _notice_event(author: str, invocation_id: str, text: str) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "author": author,
        "invocationId": invocation_id,
        "content": {"role": "model", "parts": [{"text": text}]},
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }


def _text_of_new_message(new_message: Dict[str, Any]) -> str:
    return " ".join(part.get("text", "") for part in (new_message.get("parts") or [])
                    if part.get("text"))


def _new_message_to_human_message(new_message: Dict[str, Any],
                                  text_override: str = None) -> HumanMessage:
    """Convert an ADK new_message ({role, parts}) into a LangChain HumanMessage.

    text_override replaces the user's text (guardrail redaction); attachments pass through.
    """
    parts = new_message.get("parts") or []
    blocks: List[Any] = []
    text_emitted = False
    for part in parts:
        text = part.get("text")
        if text:
            if text_override is not None:
                if not text_emitted:
                    blocks.append({"type": "text", "text": text_override})
                    text_emitted = True
            else:
                blocks.append({"type": "text", "text": text})
            continue
        inline = part.get("inline_data") or part.get("inlineData")
        if inline:
            mime = inline.get("mime_type") or inline.get("mimeType") or ""
            data = inline.get("data") or ""
            if mime.startswith("image/"):
                blocks.append({"type": "image_url",
                               "image_url": {"url": f"data:{mime};base64,{data}"}})
            elif mime.startswith("text/"):
                try:
                    blocks.append({"type": "text", "text": base64.b64decode(data).decode("utf-8", errors="replace")})
                except Exception:
                    logger.warning(f"Could not decode inline text attachment ({mime})")
            else:
                # Non-image binary attachments (e.g. PDF) are extracted to text by the
                # proxy for non-Gemini models; anything that reaches here is unsupported.
                logger.warning(f"Skipping unsupported inline attachment ({mime}) in langgraph runtime")
    blocks = [b for b in blocks if b.get("type") != "text" or b.get("text")]
    if len(blocks) == 1 and blocks[0].get("type") == "text":
        return HumanMessage(content=blocks[0]["text"])
    return HumanMessage(content=blocks)


def _user_event(new_message: Dict[str, Any], invocation_id: str) -> Dict[str, Any]:
    """The user's message persisted into session history (not streamed back)."""
    return {
        "id": str(uuid.uuid4()),
        "author": "user",
        "invocationId": invocation_id,
        "content": {"role": "user", "parts": new_message.get("parts") or []},
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }


def _log_token_usage(event: Dict[str, Any], app_name: str, user_id: str,
                     session_id: str, model_names: Dict[str, str]) -> None:
    usage = event.get("usageMetadata")
    if not usage:
        return
    author = event.get("author") or app_name
    try:
        from shared.utils.token_usage_service import get_token_usage_service
        get_token_usage_service().log_token_usage(
            request_id=event.get("invocationId") or event["id"],
            session_id=session_id,
            user_id=user_id,
            agent_name=author,
            model_name=model_names.get(author) or model_names.get(app_name),
            prompt_tokens=usage.get("prompt_token_count"),
            response_tokens=usage.get("candidates_token_count"),
        )
    except Exception as e:
        logger.warning(f"Token usage logging failed: {e}")


def _apply_output_guardrails(event: Dict[str, Any], engines: Dict[str, Any],
                             meta: Dict[str, Any]) -> None:
    """Run output guardrails on a complete event's text parts, replacing them on block/redact."""
    author = event.get("author")
    engine = engines.get(author) if engines else None
    if not engine:
        return
    from shared.utils.langgraph.hooks import check_output_guardrails
    parts = (event.get("content") or {}).get("parts") or []
    for part in parts:
        text = part.get("text")
        if not text:
            continue
        replacement = check_output_guardrails(engine, text, author, meta)
        if replacement is not None:
            part["text"] = replacement


def _client_tool_declarations(custom_metadata: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tool declarations the caller sent for this request, if any."""
    from shared.utils.tools.client_toolset import CLIENT_TOOL_METADATA_KEY
    if not isinstance(custom_metadata, dict):
        return []
    declarations = custom_metadata.get(CLIENT_TOOL_METADATA_KEY)
    if not isinstance(declarations, list):
        return []
    return [d for d in declarations if isinstance(d, dict)]


async def _resume_map(graph: Any, config: Dict[str, Any],
                      unanswered: Dict[str, Any]) -> Dict[str, Any]:
    """
    Match the caller's outstanding tool results to the interrupts the graph is
    paused on, consuming the ones that are delivered.

    Returns an empty map when the graph is not paused on anything the caller has
    answered, which is what ends the resume loop.
    """
    from shared.utils.langgraph.client_tools import pending_client_calls
    if not unanswered:
        return {}
    try:
        state = await graph.aget_state(config)
    except Exception:
        logger.exception("[LangGraph] could not read the paused state to resume client tools")
        return {}

    resume_map: Dict[str, Any] = {}
    for call in pending_client_calls(state):
        call_id, interrupt_id = call.get("id"), call.get("interrupt_id")
        if not interrupt_id or call_id not in unanswered:
            continue
        resume_map[interrupt_id] = {"result": unanswered.pop(call_id)}
    return resume_map


async def execute_run(app_name: str, user_id: str, session_id: str,
                      new_message: Dict[str, Any], invocation_id: str,
                      custom_metadata: Optional[Dict[str, Any]] = None
                      ) -> AsyncGenerator[Dict[str, Any], None]:
    store = get_session_store()
    builder = get_agent_builder()
    client_tools = _client_tool_declarations(custom_metadata)

    from shared.utils.langgraph.hooks import check_input_guardrails, check_rbac
    rbac_denial = check_rbac(user_id, app_name, session_id=session_id)
    if rbac_denial:
        rbac_denial["invocationId"] = invocation_id
        yield rbac_denial
        return

    try:
        built = await builder.get(app_name, client_tools=client_tools)
    except AgentNotFoundError:
        yield _notice_event(app_name, invocation_id,
                            f"Agent '{app_name}' is not available in the langgraph runtime. "
                            "Hardcoded (code-based) agents require AGENT_FRAMEWORK=adk.")
        return
    except UnsupportedAgentTypeError as e:
        logger.warning(str(e))
        yield _notice_event(app_name, invocation_id,
                            f"This agent type ({e.agent_type}) is not supported by the langgraph runtime yet. "
                            "Switch AGENT_FRAMEWORK to adk to use workflow agents.")
        return

    meta = {"request_id": invocation_id, "session_id": session_id, "user_id": user_id}
    config = {"configurable": {
        "thread_id": session_id,
        "user_id": user_id,
        "app_name": app_name,
    }}

    from shared.utils.langgraph.hitl import (extract_client_tool_responses,
                                             extract_confirmation_response)
    # Client tool results still to be delivered; the resume loop below pops them.
    unanswered: Dict[str, Any] = {}
    confirmation = extract_confirmation_response(new_message)
    tool_responses = extract_client_tool_responses(new_message)
    if confirmation is not None:
        # HITL resume: deliver the approve/reject decision to the paused interrupt
        from langgraph.types import Command
        graph_input = Command(resume={"confirmed": confirmation})
    elif tool_responses:
        # The caller ran the tools the graph paused on. Resume values are keyed
        # by interrupt id, which only the checkpointed state carries, so the
        # answered tool call ids have to be matched back to it.
        from langgraph.types import Command
        unanswered.update({r["id"]: r["result"] for r in tool_responses if r.get("id")})
        resume_map = await _resume_map(built.graph, config, unanswered)
        if not resume_map:
            yield _notice_event(app_name, invocation_id,
                                "This conversation is not waiting on a tool result.")
            return
        graph_input = Command(resume=resume_map)
    else:
        user_text = _text_of_new_message(new_message)
        root_engine = built.guardrail_engines.get(app_name)
        block_message = check_input_guardrails(root_engine, user_text, app_name, meta)
        store.append_event(session_id, _user_event(new_message, invocation_id))
        if block_message:
            block_event = _notice_event(app_name, invocation_id, block_message)
            store.append_event(session_id, block_event)
            yield block_event
            return
        graph_input = {"messages": [_new_message_to_human_message(
            new_message, text_override=meta.get("redacted_text"))]}

    from shared.utils.langgraph.tool_adapter import RunContext, reset_run_context, set_run_context
    run_context = RunContext(app_name=app_name, user_id=user_id, session_id=session_id,
                             agent_name=built.name, state=store.get_state(session_id))
    context_token = set_run_context(run_context)
    try:
        while True:
            stream = built.graph.astream(
                graph_input,
                config=config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
            )
            async for event, is_complete in translate_stream(
                    stream, author=built.name, invocation_id=invocation_id):
                if is_complete:
                    _apply_output_guardrails(event, built.guardrail_engines, meta)
                    artifact_delta = run_context.pop_artifact_delta()
                    if artifact_delta:
                        actions = event.setdefault("actions", {})
                        actions["artifactDelta"] = artifact_delta
                        actions["artifact_delta"] = artifact_delta
                    state_delta = run_context.pop_state_delta()
                    if state_delta:
                        store.update_state(session_id, state_delta)
                    store.append_event(session_id, event)
                    _log_token_usage(event, app_name, user_id, session_id, built.model_names)
                yield event

            # A tool node runs its calls one at a time, so a turn with several
            # client tools pauses once per call. The caller answered them all in
            # one message, so drain the rest here rather than asking again for a
            # result it has already given.
            if not unanswered:
                break
            resume_map = await _resume_map(built.graph, config, unanswered)
            if not resume_map:
                break
            from langgraph.types import Command
            graph_input = Command(resume=resume_map)
    finally:
        reset_run_context(context_token)
