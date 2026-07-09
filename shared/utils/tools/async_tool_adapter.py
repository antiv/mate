"""
Async adapter for synchronous tool callables.

google-adk's FunctionTool runs sync ``def`` tools directly on the event loop,
so a slow tool (ERP call, Google API, subprocess) freezes every other request
in the process. ``to_thread_tool`` wraps a sync callable in an ``async def``
adapter that executes it via ``asyncio.to_thread``, keeping the loop free.

The adapter preserves ``__name__``/``__doc__``/``__annotations__`` and
``inspect.signature`` so ADK builds an identical LLM function declaration
and still detects/injects ``ToolContext`` parameters.
"""

import asyncio
import functools
import inspect
from typing import Any


def _is_async_callable(fn: Any) -> bool:
    # Mirrors ADK's check in FunctionTool._invoke_callable
    return inspect.iscoroutinefunction(fn) or (
        hasattr(fn, "__call__") and inspect.iscoroutinefunction(fn.__call__)
    )


def to_thread_tool(fn: Any) -> Any:
    """
    Wrap a sync tool callable so it runs via asyncio.to_thread instead of
    blocking the event loop.

    Returned unchanged: non-callables, BaseTool/BaseToolset instances
    (e.g. MCPToolset, google_search), async callables, and (async) generator
    functions (streaming tools use ADK's live path).
    """
    try:
        from google.adk.tools.base_tool import BaseTool
        from google.adk.tools.base_toolset import BaseToolset
        if isinstance(fn, (BaseTool, BaseToolset)):
            return fn
    except ImportError:
        pass

    if not callable(fn) or _is_async_callable(fn):
        return fn
    if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
        return fn

    @functools.wraps(fn)
    async def _threaded_tool(*args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    # functools.wraps copies __name__/__doc__/__annotations__ and sets
    # __wrapped__ (inspect.signature follows it); set __signature__ explicitly
    # for consumers that don't unwrap.
    try:
        _threaded_tool.__signature__ = inspect.signature(fn)
    except (ValueError, TypeError):
        pass
    return _threaded_tool
