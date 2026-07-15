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
from typing import Any, AsyncGenerator, Dict, List

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


async def execute_run(app_name: str, user_id: str, session_id: str,
                      new_message: Dict[str, Any], invocation_id: str) -> AsyncGenerator[Dict[str, Any], None]:
    store = get_session_store()
    builder = get_agent_builder()

    from shared.utils.langgraph.hooks import check_input_guardrails, check_rbac
    rbac_denial = check_rbac(user_id, app_name, session_id=session_id)
    if rbac_denial:
        rbac_denial["invocationId"] = invocation_id
        yield rbac_denial
        return

    try:
        built = await builder.get(app_name)
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

    from shared.utils.langgraph.hitl import extract_confirmation_response
    confirmation = extract_confirmation_response(new_message)
    if confirmation is not None:
        # HITL resume: deliver the approve/reject decision to the paused interrupt
        from langgraph.types import Command
        graph_input = Command(resume={"confirmed": confirmation})
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

    stream = built.graph.astream(
        graph_input,
        config=config,
        stream_mode=["messages", "updates"],
        subgraphs=True,
    )

    from shared.utils.langgraph.tool_adapter import RunContext, reset_run_context, set_run_context
    run_context = RunContext(app_name=app_name, user_id=user_id, session_id=session_id,
                             agent_name=built.name, state=store.get_state(session_id))
    context_token = set_run_context(run_context)
    try:
        async for event, is_complete in translate_stream(stream, author=built.name, invocation_id=invocation_id):
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
    finally:
        reset_run_context(context_token)
