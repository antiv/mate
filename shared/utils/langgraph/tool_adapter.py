"""
Adapts the existing ADK-style tools (plain functions taking a `tool_context`
parameter) for use inside LangGraph react agents — without modifying the tool
modules themselves.

`MateToolContext` duck-types the narrow ToolContext surface the tools actually
use (.state, save_artifact/load_artifact, .user_id, .agent_name,
._invocation_context.{user_id,app_name,session,agent}). The per-invocation
context travels via a ContextVar set by the executor, so cached graphs stay
valid across requests.
"""

import functools
import inspect
import logging
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_current_run_context: ContextVar[Optional["RunContext"]] = ContextVar(
    "mate_langgraph_run_context", default=None)


class MateState(dict):
    """Session-state mapping that records writes so they can be flushed to lg_sessions."""

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        super().__init__(initial or {})
        self.delta: Dict[str, Any] = {}

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(key, str) and (key.startswith("user:") or key.startswith("app:")):
            logger.warning(
                f"State key '{key}' uses an ADK cross-session namespace; the langgraph "
                "runtime stores it session-scoped only")
        super().__setitem__(key, value)
        self.delta[key] = value

    def update(self, *args, **kwargs) -> None:
        merged = dict(*args, **kwargs)
        for key, value in merged.items():
            self[key] = value

    def pop_delta(self) -> Dict[str, Any]:
        delta, self.delta = self.delta, {}
        return delta


class MateToolContext:
    """Duck-typed stand-in for google.adk ToolContext, backed by MATE services."""

    def __init__(self, run_context: "RunContext"):
        self._run_context = run_context
        self.state = run_context.state
        self.user_id = run_context.user_id
        self.agent_name = run_context.agent_name
        self.session = SimpleNamespace(
            id=run_context.session_id,
            user_id=run_context.user_id,
            app_name=run_context.app_name,
        )
        self._invocation_context = SimpleNamespace(
            user_id=run_context.user_id,
            app_name=run_context.app_name,
            session=self.session,
            agent=SimpleNamespace(name=run_context.agent_name),
        )
        self.function_call_id = None

    async def save_artifact(self, filename: str, artifact: Any) -> int:
        from shared.utils.langgraph.artifact_adapter import get_artifact_adapter
        version = await get_artifact_adapter().save(
            app_name=self._run_context.app_name,
            user_id=self._run_context.user_id,
            session_id=self._run_context.session_id,
            filename=filename,
            artifact=artifact,
        )
        self._run_context.artifact_delta[filename] = version
        return version

    async def load_artifact(self, filename: str, version: Optional[int] = None) -> Any:
        from shared.utils.langgraph.artifact_adapter import get_artifact_adapter
        return await get_artifact_adapter().load(
            app_name=self._run_context.app_name,
            user_id=self._run_context.user_id,
            session_id=self._run_context.session_id,
            filename=filename,
            version=version,
        )

    def __getattr__(self, name: str) -> Any:
        # Loud diagnostic for ToolContext surface the shim doesn't cover yet
        logger.warning(f"MateToolContext: tool accessed unsupported attribute '{name}'")
        raise AttributeError(name)


class RunContext:
    """Per-/run_sse-invocation context shared by all tool calls in that run."""

    def __init__(self, app_name: str, user_id: str, session_id: str,
                 agent_name: str, state: Optional[Dict[str, Any]] = None):
        self.app_name = app_name
        self.user_id = user_id
        self.session_id = session_id
        self.agent_name = agent_name
        self.state = MateState(state)
        self.artifact_delta: Dict[str, int] = {}

    def pop_artifact_delta(self) -> Dict[str, int]:
        delta, self.artifact_delta = self.artifact_delta, {}
        return delta

    def pop_state_delta(self) -> Dict[str, Any]:
        return self.state.pop_delta()


def set_run_context(run_context: RunContext):
    return _current_run_context.set(run_context)


def reset_run_context(token) -> None:
    _current_run_context.reset(token)


def get_run_context() -> Optional[RunContext]:
    return _current_run_context.get()


def _is_async_callable(fn: Any) -> bool:
    return inspect.iscoroutinefunction(fn) or (
        hasattr(fn, "__call__") and inspect.iscoroutinefunction(fn.__call__))


def adapt_adk_tool(fn: Callable) -> Callable:
    """Wrap an ADK-style tool so LangChain never sees the tool_context parameter.

    The wrapper's signature excludes tool_context (so it is absent from the LLM
    function declaration) and a MateToolContext is injected at call time from
    the active RunContext.
    """
    try:
        signature = inspect.signature(fn)
    except (ValueError, TypeError):
        return fn

    context_params = [name for name, param in signature.parameters.items()
                      if name == "tool_context" or
                      "ToolContext" in str(param.annotation)]
    if not context_params:
        return fn
    context_param = context_params[0]

    is_async = _is_async_callable(fn)

    @functools.wraps(fn)
    async def _adapted(*args: Any, **kwargs: Any) -> Any:
        run_context = get_run_context()
        if run_context is not None:
            kwargs[context_param] = MateToolContext(run_context)
        else:
            kwargs[context_param] = None
            logger.warning(f"Tool '{getattr(fn, '__name__', fn)}' called without an active run context")
        if is_async:
            return await fn(*args, **kwargs)
        import asyncio
        return await asyncio.to_thread(fn, *args, **kwargs)

    stripped_params = [param for name, param in signature.parameters.items()
                       if name != context_param]
    _adapted.__signature__ = signature.replace(parameters=stripped_params)
    annotations = dict(getattr(fn, "__annotations__", {}))
    annotations.pop(context_param, None)
    _adapted.__annotations__ = annotations
    return _adapted


def adapt_tools(tools: List[Any]) -> List[Callable]:
    """Adapt a ToolFactory tool list for LangGraph; skips non-callable ADK objects."""
    adapted = []
    for tool in tools:
        if not callable(tool) or isinstance(tool, type):
            logger.warning(
                f"Skipping tool {tool!r}: ADK-specific tool objects are not supported "
                "by the langgraph runtime")
            continue
        try:
            from google.adk.tools.base_tool import BaseTool
            from google.adk.tools.base_toolset import BaseToolset
            if isinstance(tool, (BaseTool, BaseToolset)):
                logger.warning(
                    f"Skipping ADK tool object '{getattr(tool, 'name', tool)!r}': "
                    "not supported by the langgraph runtime")
                continue
        except ImportError:
            pass
        adapted.append(adapt_adk_tool(tool))
    return adapted
