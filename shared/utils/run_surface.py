"""
Which surface started the agent run being served, when it can't tell itself.

An A2A run carries nothing of its own in its user or session ids. The agent server's
/a2a/ middleware (shared/utils/a2a_routes.py) sets this for the request. The approval
capture plugin reads it, so a request from such a run is recorded as a2a, answered in
the inbox, rather than as a chat's.
"""

from contextvars import ContextVar, Token
from typing import Optional

_surface: ContextVar[Optional[str]] = ContextVar("mate_run_surface", default=None)


def current() -> Optional[str]:
    """"a2a", or None for a run whose ids say where it came from."""
    return _surface.get()


def set_surface(value: Optional[str]) -> Token:
    """Set the surface for the current context; returns a token for reset()."""
    return _surface.set(value)


def reset(token: Token) -> None:
    _surface.reset(token)
