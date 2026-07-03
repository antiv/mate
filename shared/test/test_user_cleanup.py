#!/usr/bin/env python3
"""
Unit tests for the prefix-less temporary user cleanup background job.
"""

import unittest
from unittest.mock import patch, Mock
import sys
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import (
    Base, User, MemorySession, MemoryEvent, Credential, PersonalAccessToken
)
from shared.utils.user_cleanup import cleanup_inactive_users


class TestUserCleanup(unittest.TestCase):

    def setUp(self):
        # Create an in-memory SQLite database for testing
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        # Mock database client
        self.db = Mock()
        self.db.get_session.side_effect = lambda: self.Session()

    @patch("shared.utils.user_cleanup.get_database_client")
    @patch.dict(os.environ, {"AUTH_USERNAME": "admin", "CLEANUP_USER_TTL_DAYS": "5"})
    def test_cleanup_removes_inactive_temporary_users(self, mock_get_db):
        mock_get_db.return_value = self.db
        session = self.Session()
        
        # 1. Create candidate temporary user (older than TTL, has an inactive session)
        user_inactive = User(
            user_id="widget_123_abc",
            roles='["widget"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(user_inactive)
        
        inactive_session = MemorySession(
            session_id="session_inactive",
            app_name="test_agent",
            user_id="widget_123_abc",
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(inactive_session)
        
        inactive_event = MemoryEvent(
            session_id="session_inactive",
            event_id="evt_123",
            content={"text": "hello"},
            created_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(inactive_event)
        
        inactive_credential = Credential(
            app_name="test_agent",
            user_id="widget_123_abc",
            credential_key="key",
            credential_data="data"
        )
        session.add(inactive_credential)

        # 2. Create an active temporary user (should NOT be deleted)
        user_active = User(
            user_id="gn_mission_active",
            roles='["user"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
            updated_at=datetime.now(timezone.utc) - timedelta(days=2)
        )
        session.add(user_active)
        
        active_session = MemorySession(
            session_id="session_active",
            app_name="test_agent",
            user_id="gn_mission_active",
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
            updated_at=datetime.now(timezone.utc) - timedelta(days=2)
        )
        session.add(active_session)

        # 3. Create a candidate user with NO sessions, created recently (should NOT be deleted)
        user_new_no_session = User(
            user_id="custom_user_new",
            roles='["user"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
        session.add(user_new_no_session)

        # 4. Create a candidate user with NO sessions, created long ago (should be deleted)
        user_old_no_session = User(
            user_id="custom_user_old",
            roles='["user"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=6),
            updated_at=datetime.now(timezone.utc) - timedelta(days=6)
        )
        session.add(user_old_no_session)

        # 5. Create a protected SSO user (has email and oauth_provider, older than TTL - should NOT be deleted)
        user_sso = User(
            user_id="sso_user",
            roles='["user"]',
            email="sso@example.com",
            oauth_provider="google",
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(user_sso)

        # 6. Create a protected user with admin role (roles contain admin, older than TTL - should NOT be deleted)
        user_admin_role = User(
            user_id="local_admin",
            roles='["admin", "user"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(user_admin_role)

        # 7. Create a protected user with an active PAT (older than TTL - should NOT be deleted)
        user_pat = User(
            user_id="pat_user",
            roles='["user"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(user_pat)
        
        pat = PersonalAccessToken(
            token_hash="hash_value",
            token_prefix="mate_pat_xyz",
            name="test_pat",
            user_id="pat_user",
            created_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(pat)

        # 8. Create the main admin AUTH_USERNAME user (older than TTL - should NOT be deleted)
        user_main_admin = User(
            user_id="admin",
            roles='["admin"]',
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        session.add(user_main_admin)

        session.commit()
        session.close()

        # Run the cleanup job
        result = cleanup_inactive_users(ttl_days=5)
        
        # Verify result output
        self.assertEqual(result["removed_users"], 2) # widget_123_abc and custom_user_old should be removed

        # Re-open session to check remaining records
        session = self.Session()
        try:
            # check widget_123_abc deletion and its cascades
            self.assertIsNone(session.query(User).filter_by(user_id="widget_123_abc").first())
            self.assertIsNone(session.query(MemorySession).filter_by(session_id="session_inactive").first())
            self.assertIsNone(session.query(MemoryEvent).filter_by(session_id="session_inactive").first())
            self.assertIsNone(session.query(Credential).filter_by(user_id="widget_123_abc").first())

            # check custom_user_old deletion
            self.assertIsNone(session.query(User).filter_by(user_id="custom_user_old").first())

            # check that protected users still exist
            self.assertIsNotNone(session.query(User).filter_by(user_id="gn_mission_active").first())
            self.assertIsNotNone(session.query(User).filter_by(user_id="custom_user_new").first())
            self.assertIsNotNone(session.query(User).filter_by(user_id="sso_user").first())
            self.assertIsNotNone(session.query(User).filter_by(user_id="local_admin").first())
            self.assertIsNotNone(session.query(User).filter_by(user_id="pat_user").first())
            self.assertIsNotNone(session.query(User).filter_by(user_id="admin").first())
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
