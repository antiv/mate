"""
Per-response metrics: how long one agent invocation took, and where it came from.

Recorded at the runtime's invocation boundary rather than in the HTTP layer. Only
two of the eight surfaces that invoke an agent go through the auth server's proxy —
the widget, the OpenAI-compatible endpoint, MCP, triggers, Slack and evals all open
their own client straight to the agent process — so the HTTP layer cannot see most
responses. The runtime sees all of them.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from .database_client import get_database_client
from .models import AgentResponse

logger = logging.getLogger(__name__)

ORIGINS = ('chat', 'api', 'trigger', 'slack', 'eval')

# Each internal caller already stamps something distinctive on the run; these are the
# markers, in one place so they can be tested and kept honest.
#   trigger  shared/utils/trigger_runner.py:339   user_id="trigger_runner"
#   eval     shared/utils/dashboard/dashboard_server.py:128  user_id="eval_runner"
#   api      server/openai_routes.py:128          session_id="openai_sess_…"
#   slack    server/slack_routes.py:272           session_id="slack_…"
# A2A and MCP carry no marker today and therefore fall under 'chat'. Splitting them
# out needs a prefix at the point those sessions are created.
_USER_ID_ORIGINS = {
    'trigger_runner': 'trigger',
    'eval_runner': 'eval',
}
_SESSION_PREFIX_ORIGINS = (
    ('openai_sess_', 'api'),
    ('slack_', 'slack'),
)


def resolve_origin(user_id: Optional[str], session_id: Optional[str]) -> str:
    """Classify an invocation so background work can be kept out of user-facing stats."""
    if user_id and user_id in _USER_ID_ORIGINS:
        return _USER_ID_ORIGINS[user_id]
    if session_id:
        for prefix, origin in _SESSION_PREFIX_ORIGINS:
            if session_id.startswith(prefix):
                return origin
    return 'chat'


def _session():
    """A DB session, or None. Never raises — this runs beside a live response."""
    try:
        db_client = get_database_client()
        if not db_client or not db_client.is_connected():
            return None
        return db_client.get_session()
    except Exception as e:
        logger.warning("Response metrics unavailable: %s", e)
        return None


def start_response(invocation_id: str, started_at: Optional[datetime] = None,
                   session_id: Optional[str] = None, user_id: Optional[str] = None,
                   agent_name: Optional[str] = None) -> bool:
    """Open a RUNNING row for an invocation.

    Written up front rather than on completion because ADK does not guarantee the
    after-run hook on a failed invocation — `_run_with_plugins` calls it after the
    event loop rather than in a finally (google/adk/runners.py:1413). A row left
    RUNNING is therefore a response that errored or never finished, which is worth
    seeing rather than losing.
    """
    session = _session()
    if not session:
        return False
    try:
        session.add(AgentResponse(
            invocation_id=invocation_id,
            session_id=session_id,
            user_id=user_id,
            agent_name=agent_name,
            origin=resolve_origin(user_id, session_id),
            started_at=started_at or datetime.now(timezone.utc),
            duration_ms=None,
            status='RUNNING',
        ))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.warning("Failed to open agent response %s: %s", invocation_id, e)
        return False
    finally:
        session.close()


def finish_response(invocation_id: str, duration_ms: int,
                    status: str = 'SUCCESS') -> bool:
    """Close the RUNNING row for an invocation with its duration."""
    session = _session()
    if not session:
        return False
    try:
        updated = session.query(AgentResponse).filter(
            AgentResponse.invocation_id == invocation_id,
            AgentResponse.status == 'RUNNING',
        ).update({'duration_ms': duration_ms, 'status': status},
                 synchronize_session=False)
        session.commit()
        return updated > 0
    except Exception as e:
        session.rollback()
        logger.warning("Failed to close agent response %s: %s", invocation_id, e)
        return False
    finally:
        session.close()
