"""
Wrapper to add tracing spans around tool invocations.
"""

import asyncio
import functools
import inspect
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)


def wrap_tool_with_tracing(tool: Callable, agent_name: str = None) -> Callable:
    """Wrap a tool function with mate.tool span when tracing is enabled.
    Skips ADK tool objects (BaseTool, MCPToolset, etc.) that are not plain functions.
    """
    try:
        from shared.utils.tracing.tracing_config import is_tracing_enabled
        if not is_tracing_enabled():
            return tool
    except Exception:
        return tool

    # Only wrap plain functions/coroutine functions. ADK tool classes (BaseTool, MCPToolset)
    # need to be passed through so _get_declaration() and inspect.signature() work.
    if not (inspect.isfunction(tool) or asyncio.iscoroutinefunction(tool)):
        return tool

    tool_name = getattr(tool, "__name__", str(tool))

    if asyncio.iscoroutinefunction(tool):

        @functools.wraps(tool)
        async def async_wrapped(*args, **kwargs):
            try:
                from opentelemetry import trace
                from shared.utils.tracing.tracer import get_tracer
                tracer = get_tracer("mate", "1.0.0")
                with tracer.start_as_current_span("mate.tool") as span:
                    span.set_attribute("mate.tool.name", tool_name)
                    if agent_name:
                        span.set_attribute("mate.agent.name", agent_name)
                    logger.debug("trace: mate.tool span started name=%s", tool_name)
                    try:
                        return await tool(*args, **kwargs)
                    finally:
                        logger.debug("trace: mate.tool span ended name=%s", tool_name)
            except Exception as e:
                logger.debug("Tool tracing error: %s", e)
                return await tool(*args, **kwargs)

        return async_wrapped
    else:

        @functools.wraps(tool)
        def sync_wrapped(*args, **kwargs):
            try:
                from opentelemetry import trace
                from shared.utils.tracing.tracer import get_tracer
                tracer = get_tracer("mate", "1.0.0")
                with tracer.start_as_current_span("mate.tool") as span:
                    span.set_attribute("mate.tool.name", tool_name)
                    if agent_name:
                        span.set_attribute("mate.agent.name", agent_name)
                    logger.debug("trace: mate.tool span started name=%s", tool_name)
                    try:
                        return tool(*args, **kwargs)
                    finally:
                        logger.debug("trace: mate.tool span ended name=%s", tool_name)
            except Exception as e:
                logger.debug("Tool tracing error: %s", e)
                return tool(*args, **kwargs)

        return sync_wrapped
