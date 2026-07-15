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
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIRMATION_RESPONSE_NAME = "adk_request_confirmation"


def wrap_with_confirmation(fn: Callable) -> Callable:
    """Pause on interrupt before executing the tool; honor the resume decision."""

    @functools.wraps(fn)
    async def _confirmed(*args: Any, **kwargs: Any) -> Any:
        from langgraph.types import interrupt

        tool_name = getattr(fn, "__name__", "tool")
        decision = interrupt({
            "originalFunctionCall": {
                "id": str(uuid.uuid4()),
                "name": tool_name,
                "args": {k: v for k, v in kwargs.items() if k != "tool_context"},
            },
            "toolConfirmation": {
                "hint": f"Please approve or reject the execution of '{tool_name}'.",
                "confirmed": False,
            },
        })
        confirmed = bool(decision.get("confirmed")) if isinstance(decision, dict) else bool(decision)
        if not confirmed:
            logger.info(f"HITL: user rejected tool '{tool_name}'")
            return f"The user rejected the execution of '{tool_name}'. Do not retry unless asked."
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    try:
        _confirmed.__signature__ = inspect.signature(fn)
    except (ValueError, TypeError):
        pass
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


def extract_confirmation_response(new_message: Dict[str, Any]) -> Optional[bool]:
    """If new_message is a HITL answer, return the confirmed flag; else None."""
    for part in (new_message or {}).get("parts") or []:
        response = part.get("function_response") or part.get("functionResponse")
        if response and response.get("name") == CONFIRMATION_RESPONSE_NAME:
            payload = response.get("response") or {}
            return bool(payload.get("confirmed"))
    return None
