#!/usr/bin/env python3
"""
Unit tests for the access-control changes:
- Agents with no roles configured are admin-only (secure default).
- Public widget visitors (user_id "widget_*") get a dedicated 'widget' role.
"""

import unittest
from unittest.mock import MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRbacSecureDefault(unittest.TestCase):
    """Empty allowed_for_roles => admin-only; explicit roles => normal matching."""

    def _middleware(self, user_roles):
        from shared.utils.rbac_middleware import RBACMiddleware
        mw = RBACMiddleware.__new__(RBACMiddleware)  # bypass __init__ (no real DB)
        us = MagicMock()
        user = MagicMock()
        user.get_roles.return_value = user_roles
        us.get_or_create_user.return_value = user
        us.check_user_access.side_effect = lambda uid, allowed: any(r in allowed for r in user_roles)
        mw.user_service = us
        return mw

    def test_empty_roles_admin_allowed(self):
        mw = self._middleware(["admin"])
        ok, _ = mw.check_agent_access("u", {"name": "a", "allowed_for_roles": []})
        self.assertTrue(ok)

    def test_empty_roles_user_denied(self):
        mw = self._middleware(["user"])
        ok, msg = mw.check_agent_access("u", {"name": "a", "allowed_for_roles": []})
        self.assertFalse(ok)
        self.assertIn("admin-only", msg)

    def test_empty_roles_widget_denied(self):
        mw = self._middleware(["widget"])
        ok, _ = mw.check_agent_access("u", {"name": "a", "allowed_for_roles": []})
        self.assertFalse(ok)

    def test_explicit_user_role_allowed(self):
        mw = self._middleware(["user"])
        ok, _ = mw.check_agent_access("u", {"name": "a", "allowed_for_roles": ["user"]})
        self.assertTrue(ok)

    def test_widget_agent_widget_user_allowed(self):
        mw = self._middleware(["widget"])
        ok, _ = mw.check_agent_access("u", {"name": "a", "allowed_for_roles": ["admin", "widget"]})
        self.assertTrue(ok)

    def test_widget_agent_dashboard_user_denied(self):
        mw = self._middleware(["user"])
        ok, _ = mw.check_agent_access("u", {"name": "a", "allowed_for_roles": ["admin", "widget"]})
        self.assertFalse(ok)


class TestWidgetRoleAssignment(unittest.TestCase):
    """get_or_create_user assigns 'widget' for widget_* ids, 'user' otherwise."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from shared.utils.models import Base
        from shared.utils.user_service import UserService

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)

        class _DB:
            def get_session(self):
                return Session()

        self.svc = UserService.__new__(UserService)
        self.svc.db_client = _DB()

    def test_widget_user_gets_widget_role(self):
        u = self.svc.get_or_create_user("widget_5_abc123")
        self.assertEqual(u.get_roles(), ["widget"])

    def test_regular_user_gets_user_role(self):
        u = self.svc.get_or_create_user("someone@example.com")
        self.assertEqual(u.get_roles(), ["user"])


if __name__ == "__main__":
    unittest.main()
