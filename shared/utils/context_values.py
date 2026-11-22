"""
Who and where a tool or callback runs: the user, app and session of an ADK context.

ToolContext and CallbackContext carry them on the invocation context; older code
paths and test doubles put them on the context itself or on its session, so each
is looked up in that order.
"""

from typing import Any, Optional, Tuple


def _invocation(context: Any) -> Any:
    return getattr(context, '_invocation_context', None) if context else None


def user_id_of(context: Any) -> Optional[str]:
    """The user of a tool or callback context; None when it has none."""
    if not context:
        return None
    invocation = _invocation(context)
    user_id = getattr(invocation, 'user_id', None) if invocation else None
    if not user_id:
        user_id = getattr(context, 'user_id', None)
    if not user_id:
        session = getattr(context, 'session', None)
        user_id = getattr(session, 'user_id', None) if session else None
    return user_id or None


def context_values(context: Any) -> Tuple[str, str, str]:
    """(app_name, user_id, session_id) of a tool context, with 'unknown', 'default', 'default' for what is missing."""
    if not context:
        return 'unknown', 'default', 'default'
    invocation = _invocation(context)
    app_name = getattr(context, 'app_name', None)
    if not app_name and invocation:
        app_name = getattr(invocation, 'app_name', None)
    session_id = None
    session = getattr(invocation, 'session', None) if invocation else None
    if session:
        session_id = getattr(session, 'id', None)
    if not session_id:
        session_id = getattr(context, 'session_id', None)
    return app_name or 'unknown', user_id_of(context) or 'default', session_id or 'default'
