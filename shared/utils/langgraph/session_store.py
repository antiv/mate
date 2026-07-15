"""
Session store for the LangGraph runtime.

Persists session metadata and completed events (in ADK Event wire shape) to the
lg_sessions / lg_events tables so the ADK-compatible session HTTP contract can
be served. Graph state itself lives in the LangGraph checkpointer.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.utils.database_client import get_database_client
from shared.utils.models import LangGraphSession, LangGraphEvent


def _session_to_wire(session_row: LangGraphSession, events: Optional[List[dict]] = None) -> Dict[str, Any]:
    """Serialize a session row to the ADK session JSON shape."""
    last_update = session_row.updated_at.replace(tzinfo=timezone.utc).timestamp() if session_row.updated_at else 0
    return {
        "id": session_row.id,
        "appName": session_row.app_name,
        "app_name": session_row.app_name,
        "userId": session_row.user_id,
        "user_id": session_row.user_id,
        "state": session_row.get_state(),
        "events": events if events is not None else [],
        "lastUpdateTime": last_update,
        "last_update_time": last_update,
    }


class SessionStore:
    """CRUD over lg_sessions / lg_events serving the ADK session wire contract."""

    def _db_session(self):
        db_client = get_database_client()
        if not db_client:
            raise RuntimeError("Database not available")
        session = db_client.get_session()
        if not session:
            raise RuntimeError("Database session failed")
        return session

    def create_session(self, app_name: str, user_id: str,
                       session_id: Optional[str] = None,
                       state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Create a session. Returns None if a session with the given id already exists."""
        db = self._db_session()
        try:
            sid = session_id or str(uuid.uuid4())
            existing = db.query(LangGraphSession).filter(LangGraphSession.id == sid).first()
            if existing:
                return None
            row = LangGraphSession(id=sid, app_name=app_name, user_id=user_id)
            row.set_state(state or {})
            db.add(row)
            db.commit()
            db.refresh(row)
            return _session_to_wire(row)
        finally:
            db.close()

    def get_session(self, app_name: str, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a session with its full event history, or None if not found."""
        db = self._db_session()
        try:
            row = db.query(LangGraphSession).filter(
                LangGraphSession.id == session_id,
                LangGraphSession.app_name == app_name,
                LangGraphSession.user_id == user_id
            ).first()
            if not row:
                return None
            events = db.query(LangGraphEvent).filter(
                LangGraphEvent.session_id == session_id
            ).order_by(LangGraphEvent.timestamp).all()
            return _session_to_wire(row, [e.to_adk_event() for e in events])
        finally:
            db.close()

    def list_sessions(self, app_name: str, user_id: str) -> List[Dict[str, Any]]:
        """List sessions for an app/user (without events), newest first."""
        db = self._db_session()
        try:
            rows = db.query(LangGraphSession).filter(
                LangGraphSession.app_name == app_name,
                LangGraphSession.user_id == user_id
            ).order_by(LangGraphSession.updated_at.desc()).all()
            return [_session_to_wire(row) for row in rows]
        finally:
            db.close()

    def delete_session(self, app_name: str, user_id: str, session_id: str) -> bool:
        """Delete a session and its events. Returns False if not found."""
        db = self._db_session()
        try:
            row = db.query(LangGraphSession).filter(
                LangGraphSession.id == session_id,
                LangGraphSession.app_name == app_name,
                LangGraphSession.user_id == user_id
            ).first()
            if not row:
                return False
            db.delete(row)
            db.commit()
            return True
        finally:
            db.close()

    def session_exists(self, app_name: str, user_id: str, session_id: str) -> bool:
        db = self._db_session()
        try:
            return db.query(LangGraphSession.id).filter(
                LangGraphSession.id == session_id,
                LangGraphSession.app_name == app_name,
                LangGraphSession.user_id == user_id
            ).first() is not None
        finally:
            db.close()

    def append_event(self, session_id: str, event: Dict[str, Any]) -> None:
        """Persist a completed event (ADK wire shape) and touch the session timestamp."""
        db = self._db_session()
        try:
            row = LangGraphEvent(
                id=event.get("id") or str(uuid.uuid4()),
                session_id=session_id,
                author=event.get("author"),
                invocation_id=event.get("invocationId"),
                content=json.dumps(event["content"]) if event.get("content") else None,
                actions=json.dumps(event["actions"]) if event.get("actions") else None,
                usage_metadata=json.dumps(event["usageMetadata"]) if event.get("usageMetadata") else None,
                timestamp=event.get("timestamp") or datetime.now(timezone.utc).timestamp(),
            )
            db.add(row)
            session_row = db.query(LangGraphSession).filter(LangGraphSession.id == session_id).first()
            if session_row:
                session_row.updated_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def update_state(self, session_id: str, state_delta: Dict[str, Any]) -> None:
        """Merge a state delta into the session state."""
        if not state_delta:
            return
        db = self._db_session()
        try:
            row = db.query(LangGraphSession).filter(LangGraphSession.id == session_id).first()
            if not row:
                return
            state = row.get_state()
            state.update(state_delta)
            row.set_state(state)
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()

    def get_state(self, session_id: str) -> Dict[str, Any]:
        db = self._db_session()
        try:
            row = db.query(LangGraphSession).filter(LangGraphSession.id == session_id).first()
            return row.get_state() if row else {}
        finally:
            db.close()


_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store
