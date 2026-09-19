"""
Caller-executed tools for the LangGraph runtime.

The OpenAI-compatible bridge lets a client (OpenCode, Continue, Cline) declare
tools it will run itself. On this runtime each becomes a StructuredTool whose
body does nothing but `interrupt()`: the graph pauses, the translator turns the
pending call into a functionCall event, and the caller's result comes back as a
function response that resumes the graph.

The same shape the require_confirmation HITL wrapper in hitl.py already uses —
only the resume value is the tool's return value rather than an approval flag.
"""

import hashlib
import json
import logging
from typing import Annotated, Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Marks an interrupt raised by a client tool, so the executor can tell it apart
# from a require_confirmation pause.
CLIENT_TOOL_INTERRUPT_KEY = "clientToolCall"

# The resume value a client tool is answered with: every result the caller has
# delivered, keyed by tool call id. LangGraph hands resume values to the
# interrupts of a task by call order, and a tool node runs its calls in
# parallel threads, so that order is not the order of the calls. Each tool
# picks its own result out of the map instead, whichever index it drew.
CLIENT_TOOL_RESULTS_KEY = "results"


def client_tools_key(declarations: Optional[List[Dict[str, Any]]]) -> str:
    """
    Cache key for a set of client tool declarations.

    Compiled graphs are cached per agent; two callers offering different tools
    need different graphs, and the same caller must not recompile every turn.
    """
    if not declarations:
        return ""
    canonical = json.dumps(
        sorted(
            ({"name": d.get("name"),
              "description": d.get("description") or "",
              "parameters": d.get("parameters") or {}}
             for d in declarations if isinstance(d, dict)),
            key=lambda d: d["name"] or "",
        ),
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _args_model(name: str, parameters: Any):
    """Pydantic args model for one tool, plus the injected tool call id."""
    from langchain_core.tools import InjectedToolCallId
    from pydantic import create_model

    from shared.utils.agent_manager import json_schema_to_pydantic_model

    base = None
    if isinstance(parameters, dict) and parameters.get("type") == "object":
        try:
            base = json_schema_to_pydantic_model(parameters, f"{name}_Args")
        except Exception as exc:
            logger.warning("Could not model the parameters of client tool '%s' (%s); "
                           "declaring it without arguments", name, exc)

    fields: Dict[str, Any] = {
        "tool_call_id": (Annotated[str, InjectedToolCallId], ...),
    }
    if base is not None:
        for field_name, field in base.model_fields.items():
            fields[field_name] = (field.annotation, field)
    return create_model(f"{name}_ClientArgs", **fields)


def build_client_tools(declarations: Optional[List[Dict[str, Any]]],
                       reserved_names: Optional[Any] = None) -> List[Any]:
    """StructuredTools that pause the graph instead of running anything."""
    if not declarations:
        return []

    from langchain_core.tools import StructuredTool

    reserved = frozenset(reserved_names or ())
    tools: List[Any] = []
    for declaration in declarations:
        if not isinstance(declaration, dict):
            continue
        name = declaration.get("name")
        if not name:
            continue
        if name in reserved:
            logger.warning("Client tool '%s' collides with a tool the agent already has; "
                           "keeping the agent's own tool", name)
            continue
        tools.append(_build_one(StructuredTool, name,
                                declaration.get("description") or "",
                                declaration.get("parameters")))
    return tools


def _build_one(structured_tool_cls: Any, name: str, description: str, parameters: Any) -> Any:
    def _call(tool_call_id: str, **kwargs: Any) -> Any:
        from langgraph.types import interrupt
        pause = {CLIENT_TOOL_INTERRUPT_KEY: {"id": tool_call_id, "name": name, "args": kwargs}}
        while True:
            # A value meant for a sibling call is skipped; the next interrupt()
            # either draws the next delivered value or pauses the graph again.
            results = interrupt(pause).get(CLIENT_TOOL_RESULTS_KEY)
            if isinstance(results, dict) and tool_call_id in results:
                return results[tool_call_id]

    _call.__name__ = name
    return structured_tool_cls.from_function(
        func=_call,
        name=name,
        description=description or f"Tool '{name}' executed by the calling client.",
        args_schema=_args_model(name, parameters),
    )


def pending_client_calls(state: Any) -> List[Dict[str, Any]]:
    """
    The client tool calls a paused graph is waiting on, as
    [{"interrupt_id", "id", "name", "args"}].

    Read off the checkpointed state rather than the event stream because the
    resume has to be addressed by interrupt id, which only the state carries.
    """
    pending: List[Dict[str, Any]] = []
    for task in getattr(state, "tasks", None) or ():
        for pending_interrupt in getattr(task, "interrupts", None) or ():
            value = getattr(pending_interrupt, "value", None)
            if not isinstance(value, dict):
                continue
            call = value.get(CLIENT_TOOL_INTERRUPT_KEY)
            if not isinstance(call, dict):
                continue
            pending.append({
                "interrupt_id": getattr(pending_interrupt, "id", None),
                "id": call.get("id"),
                "name": call.get("name"),
                "args": call.get("args") or {},
            })
    return pending
