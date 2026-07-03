"""
Utility module for cleaning up temporary/ephemeral users and their sessions.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from shared.utils.database_client import get_database_client
from shared.utils.models import User, MemorySession, MemoryEvent, Credential, PersonalAccessToken

logger = logging.getLogger(__name__)


def cleanup_inactive_users(ttl_days: int = None) -> Dict[str, Any]:
    """
    Find and delete inactive temporary/API-created users and their associated data.
    
    Excludes protected users:
    - The main admin (configured via AUTH_USERNAME environment variable)
    - SSO/OAuth authenticated users (oauth_provider / email is set)
    - Users with admin role
    - Users with active Personal Access Tokens (PATs)
    
    For candidate temporary users:
    - If they have memory sessions: delete if all their sessions are older than ttl_days.
    - If they have no memory sessions: delete if the user record is older than ttl_days.
    """
    if ttl_days is None:
        try:
            ttl_days = int(os.getenv("CLEANUP_USER_TTL_DAYS", "5"))
        except ValueError:
            ttl_days = 5

    db = get_database_client()
    session = db.get_session()
    if not session:
        logger.error("User cleanup: Failed to get database session")
        return {"error": "Failed to get database session", "removed_users": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    auth_username = os.getenv("AUTH_USERNAME", "admin")

    try:
        # Get user_ids that have Personal Access Tokens
        users_with_pats = {t.user_id for t in session.query(PersonalAccessToken).all()}

        # Find candidates for cleanup:
        # - not the main admin
        # - no oauth_provider/email
        # - does not have admin role
        candidates = session.query(User).filter(
            User.user_id != auth_username,
            User.oauth_provider.is_(None),
            User.email.is_(None),
            ~User.roles.like('%"admin"%')
        ).all()

        users_to_delete = []
        for candidate in candidates:
            # Skip if user has active PATs
            if candidate.user_id in users_with_pats:
                continue

            # Query their memory sessions
            user_sessions = session.query(MemorySession).filter(
                MemorySession.user_id == candidate.user_id
            ).all()

            if user_sessions:
                # Find the newest session update time
                newest_session_time = max(s.updated_at for s in user_sessions)
                if newest_session_time.tzinfo is None:
                    newest_session_time = newest_session_time.replace(tzinfo=timezone.utc)

                if newest_session_time < cutoff:
                    users_to_delete.append(candidate.user_id)
            else:
                # No sessions: check user creation time
                created_time = candidate.created_at
                if created_time.tzinfo is None:
                    created_time = created_time.replace(tzinfo=timezone.utc)

                if candidate.user_id.startswith("widget_") or created_time < cutoff:
                    users_to_delete.append(candidate.user_id)

        if not users_to_delete:
            return {"removed_users": 0}

        # Find session IDs of memory sessions associated with users to delete
        user_memory_sessions = session.query(MemorySession).filter(
            MemorySession.user_id.in_(users_to_delete)
        ).all()
        session_ids = [s.session_id for s in user_memory_sessions]

        # Perform deletion in order of dependencies (bottom-up to avoid FK constraint errors)
        # 1. Delete associated credentials
        session.query(Credential).filter(
            Credential.user_id.in_(users_to_delete)
        ).delete(synchronize_session=False)

        # 2. Delete memory events first (to satisfy ForeignKey constraint)
        if session_ids:
            session.query(MemoryEvent).filter(
                MemoryEvent.session_id.in_(session_ids)
            ).delete(synchronize_session=False)

        # 3. Delete memory sessions
        session.query(MemorySession).filter(
            MemorySession.user_id.in_(users_to_delete)
        ).delete(synchronize_session=False)

        # 4. Delete user records from users table
        session.query(User).filter(
            User.user_id.in_(users_to_delete)
        ).delete(synchronize_session=False)

        session.commit()
        logger.info("Cleaned up %d temporary/inactive user(s)", len(users_to_delete))
        return {"removed_users": len(users_to_delete)}

    except Exception as exc:
        session.rollback()
        logger.exception("User cleanup failed: %s", exc)
        return {"error": str(exc), "removed_users": 0}
    finally:
        session.close()
