"""
How large an event an MCP server may stream to us.

mcp 2 refuses an event over 1 MB, so a tool that returns an image of a few hundred
kilobytes more fails. Its streamable HTTP client takes the limit as a parameter, which
ADK doesn't pass, and its SSE client has none, so the parameters' defaults are changed.
shared/test/test_mcp_event_limit.py fails when an upgrade moves them.
"""

import logging

from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import sse_within_origin

logger = logging.getLogger(__name__)

# Per event, and an agent's MCP servers are its workspace's choice: bounded, so one
# can't fill the server's memory
MAX_EVENT_BYTES = 16 * 1024 * 1024


def raise_event_limit() -> None:
    """Make MAX_EVENT_BYTES the limit of both transports, for every client in the process."""
    for opener, parameter in ((streamable_http_client, "max_sse_event_size"),
                              (sse_within_origin, "max_event_size")):
        defaults = getattr(getattr(opener, "__wrapped__", None), "__kwdefaults__", None) or {}
        if parameter in defaults:
            defaults[parameter] = MAX_EVENT_BYTES
        else:
            logger.warning("MCP: %s has no %s any more; events over the SDK's own limit are refused",
                           getattr(opener, "__name__", opener), parameter)
