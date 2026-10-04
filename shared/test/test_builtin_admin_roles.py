#!/usr/bin/env python3
"""
The built-in account (AUTH_USERNAME) administers the dashboard, so it must also
hold the 'admin' role that admin-only agents and memory block writes check.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import Base, User


class TestBuiltinAdminRoles(unittest.TestCase):

    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[User.__table__])
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        db_client = MagicMock()
        db_client.get_session.side_effect = self.Session
        patcher = patch("shared.utils.user_service.get_database_client", return_value=db_client)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"AUTH_USERNAME": "boss"})
        env.start()
        self.addCleanup(env.stop)

        from shared.utils.user_service import UserService
        from shared.utils.rbac_middleware import RBACMiddleware
        self.service = UserService()
        self.mw = RBACMiddleware.__new__(RBACMiddleware)
        self.mw.user_service = self.service

    def _roles(self, user_id: str) -> list:
        session = self.Session()
        try:
            return session.query(User).filter(User.user_id == user_id).first().get_roles()
        finally:
            session.close()

    def test_new_builtin_account_reaches_admin_only_agent(self) -> None:
        ok, msg = self.mw.check_agent_access("boss", {"name": "a", "allowed_for_roles": []})
        self.assertTrue(ok, msg)
        self.assertEqual(self._roles("boss"), ["admin", "user"])

    def test_existing_builtin_row_without_admin_is_corrected(self) -> None:
        session = self.Session()
        session.add(User(user_id="boss", roles='["user"]'))
        session.commit()
        session.close()

        ok, msg = self.mw.check_agent_access("boss", {"name": "a", "allowed_for_roles": []})
        self.assertTrue(ok, msg)
        self.assertEqual(self._roles("boss"), ["user", "admin"])
        self.assertIn("admin", self.service.get_user_roles("boss"))

    def test_other_users_still_get_user_role(self) -> None:
        ok, _ = self.mw.check_agent_access("someone", {"name": "a", "allowed_for_roles": []})
        self.assertFalse(ok)
        self.assertEqual(self._roles("someone"), ["user"])

    def test_widget_visitor_named_like_builtin_is_not_admin(self) -> None:
        self.service.get_or_create_user("widget_k_boss")
        self.assertEqual(self._roles("widget_k_boss"), ["widget"])


if __name__ == "__main__":
    unittest.main()
