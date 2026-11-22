"""
Human-in-the-loop tool confirmation for the LangGraph runtime.

Tools listed in tool_config['require_confirmation'] are wrapped so execution
pauses on a LangGraph interrupt. The translator turns the interrupt into an
adk_request_confirmation functionCall event (the same shape ADK emits), the
frontend shows an approve/reject card, and the answer comes back through
/run_sse as a function_response part which resumes the graph.
"""

import functools
import inspect
import logging
import uuid
from typing import Annotated, Any, Callable, Dict, List

logger = logging.getLogger(__name__)

CONFIRMATION_RESPONSE_NAME = "adk_request_confirmation"

# The resume value a paused tool is answered with: every decision the caller
# has given, keyed by confirmation call id. Parallel calls pause together, and
# each is resumed with the whole map and takes its own decision out of it.
CONFIRMATIONS_KEY = "confirmations"


def confirmation_call_id(tool_call_id: str) -> str:
    """Id of the adk_request_confirmation call for a tool call; the answer carries it.

    Derived from the model's tool call id, so it stays the same each time
    LangGraph re-runs the paused tool, and is distinct from it, as on ADK.
    """
    return f"adk-confirm-{tool_call_id}"


def wrap_with_confirmation(fn: Callable) -> Callable:
    """Pause on interrupt before executing the tool; honor the resume decision."""
    from langchain_core.tools import InjectedToolCallId

    @functools.wraps(fn)
    async def _confirmed(*args: Any, tool_call_id: str = "", **kwargs: Any) -> Any:
        from langgraph.types import interrupt

        tool_name = getattr(fn, "__name__", "tool")
        call_id = tool_call_id or str(uuid.uuid4())
        confirmation_id = confirmation_call_id(call_id)
        pause = {
            "originalFunctionCall": {
                "id": call_id,
                "name": tool_name,
                "args": {k: v for k, v in kwargs.items() if k != "tool_context"},
            },
            "toolConfirmation": {
                "hint": f"Please approve or reject the execution of '{tool_name}'.",
                "confirmed": False,
            },
        }
        while True:
            # Resumed with a sibling's decision only: pause again for our own.
            resume = interrupt(pause)
            decisions = resume.get(CONFIRMATIONS_KEY) if isinstance(resume, dict) else None
            if isinstance(decisions, dict) and confirmation_id in decisions:
                break
        if not decisions[confirmation_id]:
            logger.info(f"HITL: user rejected tool '{tool_name}'")
            return f"The user rejected the execution of '{tool_name}'. Do not retry unless asked."
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    try:
        signature = inspect.signature(fn)
    except (ValueError, TypeError):
        return _confirmed
    # LangGraph fills tool_call_id with the model's call id and keeps it out of
    # the declaration the model sees.
    injected = inspect.Parameter("tool_call_id", inspect.Parameter.KEYWORD_ONLY,
                                 annotation=Annotated[str, InjectedToolCallId])
    params = [p for p in signature.parameters.values() if p.kind != p.VAR_KEYWORD]
    params += [injected] + [p for p in signature.parameters.values() if p.kind == p.VAR_KEYWORD]
    try:
        _confirmed.__signature__ = signature.replace(parameters=params)
    except ValueError:
        _confirmed.__signature__ = signature
        return _confirmed
    _confirmed.__annotations__ = {**getattr(fn, "__annotations__", {}),
                                  "tool_call_id": injected.annotation}
    return _confirmed


def apply_confirmation_wrapping(tools: List[Any], confirm_names: List[str],
                                agent_name: str = "unknown") -> List[Any]:
    if not confirm_names:
        return tools
    wrapped = []
    for tool in tools:
        tool_name = getattr(tool, "__name__", None) or getattr(tool, "name", None)
        if tool_name in confirm_names and callable(tool):
            wrapped.append(wrap_with_confirmation(tool))
            logger.info(f"Tool {tool_name} requires user confirmation (agent {agent_name})")
        else:
            wrapped.append(tool)
    return wrapped


def extract_confirmation_responses(new_message: Dict[str, Any]) -> Dict[str, bool]:
    """The approve/reject answers in new_message, as {confirmation call id: confirmed}."""
    decisions: Dict[str, bool] = {}
    for part in (new_message or {}).get("parts") or []:
        response = part.get("function_response") or part.get("functionResponse")
        if response and response.get("name") == CONFIRMATION_RESPONSE_NAME and response.get("id"):
            payload = response.get("response") or {}
            decisions[response["id"]] = bool(payload.get("confirmed"))
    return decisions


def pending_confirmations(state: Any) -> List[Dict[str, Any]]:
    """
    The confirmations a paused graph is waiting on, as
    [{"interrupt_id", "confirmation_id"}].

    Read off the checkpointed state, like client tool calls, because the resume
    has to be addressed by interrupt id.
    """
    from shared.utils.langgraph.client_tools import finished_tool_call_ids
    pending: List[Dict[str, Any]] = []
    finished = finished_tool_call_ids(state)
    for task in getattr(state, "tasks", None) or ():
        for pending_interrupt in getattr(task, "interrupts", None) or ():
            value = getattr(pending_interrupt, "value", None)
            call = value.get("originalFunctionCall") if isinstance(value, dict) else None
            if isinstance(call, dict) and call.get("id") and call["id"] not in finished:
                pending.append({
                    "interrupt_id": getattr(pending_interrupt, "id", None),
                    "confirmation_id": confirmation_call_id(call["id"]),
                })
    return pending


def extract_client_tool_responses(new_message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Tool results the caller is returning, as [{"id", "name", "result"}].

    Anything that is not the HITL confirmation answer is treated as a result for
    a client-executed tool: those are the only other function responses that can
    reach this runtime from outside.
    """
    responses = []
    for part in (new_message or {}).get("parts") or []:
        response = part.get("function_response") or part.get("functionResponse")
        if not response or response.get("name") == CONFIRMATION_RESPONSE_NAME:
            continue
        payload = response.get("response")
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]
        responses.append({
            "id": response.get("id"),
            "name": response.get("name"),
            "result": payload,
        })
    return responses
