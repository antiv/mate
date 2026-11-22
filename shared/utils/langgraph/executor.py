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
                     session_id: str, model_names: Dict[str, str],
                     fallback_models: Optional[Dict[str, str]] = None) -> None:
    author = event.get("author") or app_name
    model_name = model_names.get(author) or model_names.get(app_name)
    fallback = (fallback_models or {}).get(author)
    is_fallback = False
    if fallback:
        from shared.utils.langgraph.model_factory import litellm_model_name
        if event.get("modelVersion") == litellm_model_name(fallback):
            from shared.callbacks.model_fallback_callback import record_model_fallback
            record_model_fallback(author, user_id, model_name, fallback, None)
            model_name = fallback
            is_fallback = True
    usage = event.get("usageMetadata")
    if not usage:
        return
    try:
        from shared.utils.token_usage_service import get_token_usage_service
        get_token_usage_service().log_token_usage(
            request_id=event.get("invocationId") or event["id"],
            session_id=session_id,
            user_id=user_id,
            agent_name=author,
            model_name=model_name,
            prompt_tokens=usage.get("prompt_token_count"),
            response_tokens=usage.get("candidates_token_count"),
            is_fallback=is_fallback,
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
                      answers: Dict[str, Any],
                      decisions: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """
    Resume values for the interrupts the graph is paused on, when the caller
    has answered them.

    Every paused client call is handed the whole answer map: the tool picks its
    own result by call id, because LangGraph matches resume values to
    interrupts by call order and parallel tool calls do not pause in a fixed
    order. Approve/reject decisions for require_confirmation tools work the same
    way, by confirmation call id. Returns an empty map when the graph is not
    paused on anything the caller has answered, which is what ends the resume
    loop.
    """
    from shared.utils.langgraph.client_tools import (CLIENT_TOOL_RESULTS_KEY,
                                                     pending_client_calls)
    from shared.utils.langgraph.hitl import CONFIRMATIONS_KEY, pending_confirmations
    if not answers and not decisions:
        return {}
    try:
        state = await graph.aget_state(config)
    except Exception:
        logger.exception("[LangGraph] could not read the paused state to resume")
        return {}

    resume_map: Dict[str, Any] = {}
    for call in pending_client_calls(state):
        call_id, interrupt_id = call.get("id"), call.get("interrupt_id")
        if interrupt_id and call_id in answers:
            resume_map[interrupt_id] = {CLIENT_TOOL_RESULTS_KEY: answers}
    for pending in pending_confirmations(state) if decisions else ():
        if pending["interrupt_id"] and pending["confirmation_id"] in decisions:
            resume_map[pending["interrupt_id"]] = {CONFIRMATIONS_KEY: decisions}
    return resume_map


async def _already_sent_ids(graph: Any, config: Dict[str, Any]) -> set:
    """
    Ids of what the caller already has before this turn resumes the graph: the
    confirmations it is paused on, and the tool calls that have finished.
    """
    from shared.utils.langgraph.client_tools import finished_tool_call_ids
    from shared.utils.langgraph.hitl import pending_confirmations
    try:
        state = await graph.aget_state(config)
    except Exception:
        return set()
    return ({p["confirmation_id"] for p in pending_confirmations(state)}
            | finished_tool_call_ids(state))


def _repeats_what_was_sent(event: Dict[str, Any], already_sent: set) -> bool:
    """
    An event made only of calls or results the caller already has.

    Resuming one of several paused calls re-runs the others, and each pauses
    again for its own answer: the chat already shows a card for it. Once the
    last one is answered, the results of the ones answered earlier come again.
    """
    parts = (event.get("content") or {}).get("parts") or []
    ids = []
    for part in parts:
        item = part.get("functionCall") or part.get("functionResponse")
        if not item:
            return False
        ids.append(item.get("id"))
    return bool(ids) and all(i in already_sent for i in ids)


async def _record_requests(event: Dict[str, Any], app_name: str, user_id: str, session_id: str,
                           invocation_id: str, state: Any) -> None:
    """
    A pending approval row for each adk_request_confirmation call in event. A call
    the chat may not answer gets "mateApprover" in its args, as on ADK
    (shared/callbacks/approval_plugin.py), so call this before the event is stored.
    """
    import asyncio

    from shared.utils import approval_service
    parts = (event.get("content") or {}).get("parts") or []
    calls = list(approval_service.confirmation_calls(parts))
    if not calls:
        return
    trigger_id = state.get(approval_service.TRIGGER_STATE_KEY) if hasattr(state, "get") else None
    for call in calls:
        request_id = await asyncio.to_thread(
            approval_service.record_request, confirmation_call_id=call["id"], args=call["args"],
            agent_name=event.get("author"), app_name=app_name, user_id=user_id,
            session_id=session_id, invocation_id=invocation_id, trigger_id=trigger_id)
        answered_in = await asyncio.to_thread(approval_service.answering, request_id)
        if answered_in != "chat" and isinstance(call["args"], dict):
            call["args"]["mateApprover"] = answered_in


async def _gate_decisions(decisions: Dict[str, bool], session_id: str,
                          user_id: str) -> Dict[str, bool]:
    """The answers the run may receive (approval_service.gate_answer): decided, or left out."""
    import asyncio

    from shared.utils import approval_service
    allowed: Dict[str, bool] = {}
    for call_id, confirmed in decisions.items():
        answer = await asyncio.to_thread(
            approval_service.gate_answer, session_id=session_id,
            confirmation_call_id=call_id, confirmed=confirmed, answered_by=user_id)
        if answer is not None:
            allowed[call_id] = answer
    return allowed


async def _resumed_agent(built: Any, config: Dict[str, Any]) -> str:
    """The agent this turn runs in: the one the conversation was transferred to.

    A multi-agent graph starts each turn at the checkpointed current_agent (its
    route_entry), and a paused tool call resumes inside that agent's node. Raises
    when the checkpoint cannot be read, so the caller refuses rather than guesses.
    """
    if len(built.model_names) <= 1:
        return built.name
    state = await built.graph.aget_state(config)
    current = (state.values or {}).get("current_agent")
    return current if current in built.model_names else built.name


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

    # The check above is for the agent requested. After a transfer the turn runs
    # in the sub-agent instead, whose roles may differ or have changed since: ADK
    # checks the agent that answers on every model call, so check it here too.
    try:
        resumed_agent = await _resumed_agent(built, config)
    except Exception:
        logger.exception(f"[LangGraph] could not read which agent session {session_id} resumes in")
        from shared.utils.langgraph.hooks import _access_denied_event
        rbac_denial = _access_denied_event(
            "Access could not be verified. Please try again.", user_id, app_name)
    else:
        rbac_denial = (check_rbac(user_id, resumed_agent, session_id=session_id)
                       if resumed_agent != app_name else None)
    if rbac_denial:
        rbac_denial["invocationId"] = invocation_id
        yield rbac_denial
        return

    from shared.utils.langgraph.hitl import (extract_client_tool_responses,
                                             extract_confirmation_responses)
    # Client tool results the caller delivered, by tool call id.
    answers: Dict[str, Any] = {}
    # Approve/reject decisions the caller gave, by confirmation call id.
    decisions = extract_confirmation_responses(new_message)
    tool_responses = extract_client_tool_responses(new_message)
    already_sent: set = set()
    if decisions or tool_responses:
        # The caller answered what the graph paused on: decisions for
        # require_confirmation tools, results for client tools. Resume values
        # are keyed by interrupt id, which only the checkpointed state carries,
        # so the answered call ids have to be matched back to it.
        from langgraph.types import Command
        answers.update({r["id"]: r["result"] for r in tool_responses if r.get("id")})
        answered_approval = bool(decisions)
        if decisions:
            already_sent = await _already_sent_ids(built.graph, config)
            # Only the answers to what the graph is paused on, and only as gate_answer
            # allows: once, and by whoever may give them
            asked = {cid: ok for cid, ok in decisions.items() if cid in already_sent}
            decisions = await _gate_decisions(asked, session_id, user_id)
            if asked and not decisions and not tool_responses:
                yield _notice_event(app_name, invocation_id,
                                    "This approval is decided in the approvals inbox, "
                                    "or was answered already.")
                return
        resume_map = await _resume_map(built.graph, config, answers, decisions)
        if not resume_map:
            yield _notice_event(app_name, invocation_id,
                                "This conversation is not waiting on that approval." if answered_approval
                                else "This conversation is not waiting on a tool result.")
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
                if already_sent and _repeats_what_was_sent(event, already_sent):
                    continue
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
                    await _record_requests(event, app_name, user_id, session_id, invocation_id,
                                           run_context.state)
                    store.append_event(session_id, event)
                    _log_token_usage(event, app_name, user_id, session_id, built.model_names,
                                     built.fallback_models)
                yield event

            # A tool node surfaces one pause at a time, so a turn with several
            # client tools pauses once per call. The caller answered them all in
            # one message, so drain the rest here rather than asking again for a
            # result it has already given. Each round satisfies one more call,
            # so this ends after at most as many rounds as there were calls.
            resume_map = await _resume_map(built.graph, config, answers, decisions)
            if not resume_map:
                break
            from langgraph.types import Command
            graph_input = Command(resume=resume_map)
    finally:
        reset_run_context(context_token)
