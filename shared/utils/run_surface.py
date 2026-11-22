"""
Which surface started the agent run being served, when it can't tell itself.

A2A and MCP runs carry nothing of their own in their user or session ids. The
agent server's /a2a/ middleware (shared/utils/a2a_routes.py) sets this for the
request, and MATE's MCP bridge, which calls agents over A2A, marks its calls with
SURFACE_HEADER. The approval capture plugin reads it, so a request from such a run
is recorded as a2a or mcp, answered in the inbox, rather than as a chat's.
"""

from contextvars import ContextVar
from typing import Optional

# Sent by MATE's MCP bridge on its A2A calls; the auth server's proxy drops it from
# what clients send.
SURFACE_HEADER = "x-mate-surface"

_surface: ContextVar[Optional[str]] = ContextVar("mate_run_surface", default=None)


def current() -> Optional[str]:
    """"a2a", "mcp", or None for a run whose ids say where it came from."""
    return _surface.get()


def set_surface(value: Optional[str]):
    """Set the surface for the current context; returns a token for reset()."""
    return _surface.set(value)


def reset(token) -> None:
    _surface.reset(token)
