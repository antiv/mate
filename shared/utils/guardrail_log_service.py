"""
Service for logging guardrail trigger events to the database.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
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


_guardrail_log_service: Optional[GuardrailLogService] = None


def get_guardrail_log_service() -> GuardrailLogService:
    global _guardrail_log_service
    if _guardrail_log_service is None:
        _guardrail_log_service = GuardrailLogService()
    return _guardrail_log_service
