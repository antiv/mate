"""
Service for logging guardrail trigger events to the database.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func

from .database_client import get_database_client
from .models import GuardrailLog

logger = logging.getLogger(__name__)


class GuardrailLogService:
    """Persists guardrail trigger events to guardrail_logs table."""

    def __init__(self):
        self.db_client = get_database_client()

    def log_trigger(
        self,
        request_id: str,
        guardrail_type: str,
        phase: str,
        action_taken: str,
        agent_name: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        matched_content: Optional[str] = None,
        details: Optional[str] = None,
    ) -> bool:
        session = self.db_client.get_session()
        if not session:
            logger.warning("No DB session — guardrail log dropped")
            return False
        try:
            log = GuardrailLog(
                request_id=request_id,
                session_id=session_id,
                user_id=user_id,
                agent_name=agent_name,
                guardrail_type=guardrail_type,
                phase=phase,
                action_taken=action_taken,
                matched_content=matched_content[:2000] if matched_content else None,
                details=details[:2000] if details else None,
                timestamp=datetime.now(timezone.utc),
            )
            session.add(log)
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to log guardrail trigger: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def count_triggers_since(
        self,
        since: datetime,
        agent_names: Optional[List[str]] = None,
        guardrail_type: Optional[str] = None,
        action_taken: Optional[str] = None,
    ) -> int:
        """Count guardrail hits since a datetime. Used by the alert engine.

        agent_names is a list so a project-scoped rule can pass all of its agents —
        guardrail_logs has no project_id.
        """
        session = self.db_client.get_session() if self.db_client else None
        if not session:
            return 0
        try:
            query = session.query(func.count(GuardrailLog.id)).filter(
                GuardrailLog.timestamp >= since)
            if agent_names is not None:
                if not agent_names:
                    return 0
                query = query.filter(GuardrailLog.agent_name.in_(agent_names))
            if guardrail_type:
                query = query.filter(GuardrailLog.guardrail_type == guardrail_type)
            if action_taken:
                query = query.filter(GuardrailLog.action_taken == action_taken)
            return int(query.scalar() or 0)
        except Exception as e:
            logger.error(f"Failed to count guardrail triggers: {e}")
            return 0
        finally:
            session.close()


_guardrail_log_service: Optional[GuardrailLogService] = None


def get_guardrail_log_service() -> GuardrailLogService:
    global _guardrail_log_service
    if _guardrail_log_service is None:
        _guardrail_log_service = GuardrailLogService()
    return _guardrail_log_service
