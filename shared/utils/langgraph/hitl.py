"""
Human-in-the-loop tool confirmation for the LangGraph runtime.

Tools listed in tool_config['require_confirmation'] are wrapped so execution
pauses on a LangGraph interrupt. The translator turns the interrupt into an
adk_request_confirmation functionCall event (the same shape ADK emits), the
frontend shows an approve/reject card, and the answer comes back through
/run_sse as a function_response part which resumes the graph.
"""

import functools
import hashlib
import inspect
import json
import logging
import uuid
from typing import Annotated, Any, Callable, Dict, List

logger = logging.getLogger(__name__)

CONFIRMATION_RESPONSE_NAME = "adk_request_confirmation"

# The resume value a paused tool is answered with: every decision the caller
# has given, keyed by confirmation call id. Parallel calls pause together, and
# each is resumed with the whole map and takes its own decision out of it.
CONFIRMATIONS_KEY = "confirmations"


# Where a pause keeps its confirmation call id; not part of what the chat is sent
CONFIRMATION_ID_KEY = "confirmationId"


def confirmation_call_id(tool_call_id: str, tool_name: str, args: Dict[str, Any]) -> str:
    """Id of the adk_request_confirmation call for a tool call; the answer carries it.

    Derived from the call, so it stays the same each time LangGraph re-runs the
    paused tool. The tool and its arguments are part of it: some models give
    two calls of a conversation the same id, and an approval of one must not
    run the other.
    """
    call = json.dumps([tool_call_id, tool_name, args], sort_keys=True, default=str)
    return f"adk-confirm-{hashlib.sha256(call.encode('utf-8')).hexdigest()[:32]}"


def _approved(call_id: str, tool_name: str, args: Dict[str, Any]) -> bool:
    """Pause the run for this tool call until it is answered; whether it may run."""
    from langgraph.types import interrupt

    confirmation_id = confirmation_call_id(call_id, tool_name, args)
    pause = {
        CONFIRMATION_ID_KEY: confirmation_id,
        "originalFunctionCall": {"id": call_id, "name": tool_name, "args": args},
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
        return False
    return True


def _rejection(tool_name: str) -> str:
    return f"The user rejected the execution of '{tool_name}'. Do not retry unless asked."


def wrap_with_confirmation(fn: Callable) -> Callable:
    """Pause on interrupt before executing the tool; honor the resume decision."""
    from langchain_core.tools import InjectedToolCallId

    @functools.wraps(fn)
    async def _confirmed(*args: Any, tool_call_id: str = "", **kwargs: Any) -> Any:
        tool_name = getattr(fn, "__name__", "tool")
        tool_args = {k: v for k, v in kwargs.items() if k != "tool_context"}
        if not _approved(tool_call_id or str(uuid.uuid4()), tool_name, tool_args):
            return _rejection(tool_name)
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


def _confirmed_tool(tool: Any) -> Any:
    """A LangChain tool (an MCP server's) that pauses before each call."""
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import BaseTool

    class ConfirmedTool(BaseTool):
        inner: Any = None

        def _run(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError("this tool asks first, which needs ainvoke()")

        async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            called = isinstance(input, dict) and input.get("type") == "tool_call"
            call_id = (input.get("id") if called else None) or str(uuid.uuid4())
            args = (input["args"] if called else input) or {}
            if not _approved(call_id, self.name, args if isinstance(args, dict) else {"input": args}):
                text = _rejection(self.name)
                return ToolMessage(text, tool_call_id=call_id, name=self.name) if called else text
            return await self.inner.ainvoke(input, config, **kwargs)

    return ConfirmedTool(name=tool.name, description=tool.description, args_schema=tool.args_schema,
                         response_format=tool.response_format, metadata=tool.metadata, inner=tool)


def apply_confirmation_wrapping(tools: List[Any], confirm_names: List[str],
                                agent_name: str = "unknown") -> List[Any]:
    """The tools, those named asking first. One that can't is left out: a tool an
    administrator said must ask never runs without asking."""
    if not confirm_names:
        return tools
    from langchain_core.tools import BaseTool

    wrapped = []
    for tool in tools:
        tool_name = getattr(tool, "__name__", None) or getattr(tool, "name", None)
        if tool_name not in confirm_names:
            wrapped.append(tool)
            continue
        if isinstance(tool, BaseTool):
            wrapped.append(_confirmed_tool(tool))
        elif callable(tool):
            wrapped.append(wrap_with_confirmation(tool))
        else:
            logger.error(f"Tool {tool_name} of agent {agent_name} is left out: it is in "
                         f"require_confirmation, and a {type(tool).__name__} can't ask")
            continue
        logger.info(f"Tool {tool_name} requires user confirmation (agent {agent_name})")
    return wrapped


def extract_confirmation_responses(new_message: Dict[str, Any]) -> Dict[str, bool]:
    """The approve/reject answers in new_message, as {confirmation call id: confirmed}."""
    decisions: Dict[str, bool] = {}
    for part in (new_message or {}).get("parts") or []:
        response = part.get("function_response") or part.get("functionResponse")
        if response and response.get("name") == CONFIRMATION_RESPONSE_NAME and response.get("id"):
            # As ADK reads it, wrapper and string booleans included
            from shared.utils.approval_service import answer_confirmed
            decisions[response["id"]] = answer_confirmed(response.get("response") or {})
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
                    "confirmation_id": value.get(CONFIRMATION_ID_KEY),
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
