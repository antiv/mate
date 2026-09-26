"""
Response feedback: a visitor's thumbs up/down on one agent response.

Ratings are keyed by (session_id, message_id) where message_id is the invocation id,
which is what lets a rating be joined to that response's cost and latency. Rating the
same message again changes the existing row rather than adding another, so the
satisfaction rate counts responses, not clicks.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .database_client import get_database_client
from .models import ResponseFeedback, TestCase

logger = logging.getLogger(__name__)

RATINGS = ('up', 'down')
MAX_COMMENT = 2000


def _event_texts(event: Dict[str, Any]) -> List[str]:
    """Visible text parts of one event; model thoughts are not part of the answer."""
    parts = (event.get('content') or {}).get('parts') or []
    return [p['text'] for p in parts
            if isinstance(p, dict) and p.get('text') and not p.get('thought')]


def extract_exchange(events: List[Dict[str, Any]],
                     invocation_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    The user's question and the agent's answer for one invocation of a session.

    Events come in the ADK shape from either runtime: ADK's own store serialises the
    id as `invocation_id`, the LangGraph store as `invocationId`. Streaming leaves
    `partial` events in some histories, and the final event repeats their text, so
    they are skipped. Returns None for a side that is not in the history.
    """
    question: List[str] = []
    answer: List[str] = []
    for event in events or []:
        if (event.get('invocation_id') or event.get('invocationId')) != invocation_id:
            continue
        if event.get('partial'):
            continue
        texts = _event_texts(event)
        if event.get('author') == 'user':
            question.extend(texts)
        else:
            answer.extend(texts)
    return ('\n\n'.join(question) or None, '\n\n'.join(answer) or None)


class FeedbackService:
    """Upsert and read response ratings."""

    def __init__(self):
        self.db_client = get_database_client()

    def _get_session(self):
        if not self.db_client or not self.db_client.is_connected():
            return None
        return self.db_client.get_session()

    def submit(self, session_id: str, message_id: str, rating: str,
               agent_name: Optional[str] = None, project_id: Optional[int] = None,
               comment: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Record or change a rating. Returns the stored row, or None on failure."""
        if rating not in RATINGS:
            return None
        if not session_id or not message_id:
            return None

        session = self._get_session()
        if not session:
            return None
        try:
            existing = session.query(ResponseFeedback).filter(
                ResponseFeedback.session_id == session_id,
                ResponseFeedback.message_id == message_id,
            ).first()

            if existing:
                existing.rating = rating
                # A visitor changing their mind should not silently drop the comment
                # they wrote, but an explicit new comment replaces it.
                if comment is not None:
                    existing.comment = comment[:MAX_COMMENT] or None
                existing.updated_at = datetime.now(timezone.utc)
                row = existing
            else:
                row = ResponseFeedback(
                    session_id=session_id,
                    message_id=message_id,
                    agent_name=agent_name,
                    project_id=project_id,
                    rating=rating,
                    comment=(comment or '')[:MAX_COMMENT] or None,
                )
                session.add(row)

            session.commit()
            session.refresh(row)
            return row.to_dict()
        except Exception as e:
            session.rollback()
            logger.error("Failed to submit feedback for %s/%s: %s",
                         session_id, message_id, e)
            return None
        finally:
            session.close()

    def get_for_session(self, session_id: str) -> Dict[str, str]:
        """message_id -> rating for one session, so a reloaded chat shows its ratings."""
        session = self._get_session()
        if not session:
            return {}
        try:
            rows = session.query(
                ResponseFeedback.message_id, ResponseFeedback.rating
            ).filter(ResponseFeedback.session_id == session_id).all()
            return {r[0]: r[1] for r in rows}
        except Exception as e:
            logger.error("Failed to read feedback for session %s: %s", session_id, e)
            return {}
        finally:
            session.close()


    def list_rated_down(self, agent_name: Optional[str] = None,
                        limit: int = 50) -> List[Dict[str, Any]]:
        """
        Most recent thumbs-down ratings, each with the active test case made from
        it, if any, as `test_case_id`.
        """
        session = self._get_session()
        if not session:
            return []
        try:
            query = session.query(ResponseFeedback).filter(ResponseFeedback.rating == 'down')
            if agent_name:
                query = query.filter(ResponseFeedback.agent_name == agent_name)
            rows = query.order_by(ResponseFeedback.updated_at.desc()).limit(limit).all()

            ids = [r.id for r in rows]
            linked = {}
            if ids:
                for tc_id, fb_id in session.query(TestCase.id, TestCase.source_feedback_id).filter(
                        TestCase.source_feedback_id.in_(ids),
                        TestCase.is_active.is_(True)).all():
                    linked[fb_id] = tc_id

            result = []
            for r in rows:
                item = r.to_dict()
                item['test_case_id'] = linked.get(r.id)
                result.append(item)
            return result
        except Exception as e:
            logger.error("Failed to list rated-down responses: %s", e)
            return []
        finally:
            session.close()

    def rated_down_agents(self) -> List[str]:
        """Agents with at least one thumbs-down, for the filter."""
        session = self._get_session()
        if not session:
            return []
        try:
            rows = session.query(ResponseFeedback.agent_name).filter(
                ResponseFeedback.rating == 'down',
                ResponseFeedback.agent_name.isnot(None),
            ).distinct().all()
            return sorted(r[0] for r in rows)
        except Exception as e:
            logger.error("Failed to list agents with rated-down responses: %s", e)
            return []
        finally:
            session.close()


_feedback_service: Optional[FeedbackService] = None


def get_feedback_service() -> FeedbackService:
    global _feedback_service
    if _feedback_service is None:
        _feedback_service = FeedbackService()
    return _feedback_service
