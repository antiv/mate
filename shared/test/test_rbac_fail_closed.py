#!/usr/bin/env python3
"""
Unit tests for RBAC failing closed.

The ADK callback caught every exception and allowed the request "to prevent
system breakage". A new user's ORM row came back detached from its session, so
reading its roles to log a denial raised, and the denial became an allow: the
first request of every new user to an admin-only agent went through. The
LangGraph hook copied the fail-open on purpose, to match.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.models import AgentConfig, Base


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db_client = MagicMock()
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.addCleanup(self.engine.dispose)

    def _user_service(self):
        with patch("shared.utils.user_service.get_database_client", return_value=self.db_client):
            from shared.utils.user_service import UserService
            return UserService()


class TestNewUserIsUsable(_Db):

    def test_a_created_user_can_be_read_after_its_session_closes(self):
        user = self._user_service().get_or_create_user("brand_new")
        self.assertEqual(user.get_roles(), ["user"])


class TestAdkCallback(_Db):

    def _run(self, user_id, agent_row):
        from shared.callbacks import rbac_callback
        from shared.utils.rbac_middleware import RBACMiddleware
        users = self._user_service()
        with patch("shared.utils.rbac_middleware.get_user_service", return_value=users):
            middleware = RBACMiddleware()
        manager = MagicMock()
        manager.get_root_agent_by_name.return_value = agent_row
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id=user_id),
                                  agent_name=agent_row.name)
        with patch.object(rbac_callback, "get_user_service", return_value=users), \
                patch.object(rbac_callback, "get_rbac_middleware", return_value=middleware), \
                patch("shared.utils.agent_manager.get_agent_manager", return_value=manager), \
                patch.object(rbac_callback, "_log_access_denied_event"):
            return rbac_callback.rbac_before_model_callback(context, MagicMock())

    def test_a_new_users_first_request_to_an_admin_only_agent_is_denied(self):
        admin_only = AgentConfig(name="admin_bot", allowed_for_roles=None)
        response = self._run("first_timer", admin_only)
        self.assertIsNotNone(response)
        self.assertEqual(response.error_code, "RBAC_ACCESS_DENIED")

    def test_a_user_with_the_role_is_allowed(self):
        open_agent = AgentConfig(name="user_bot", allowed_for_roles='["user"]')
        self.assertIsNone(self._run("first_timer", open_agent))

    def test_an_error_during_the_check_denies(self):
        from shared.callbacks import rbac_callback
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="u"),
                                  agent_name="some_bot")
        with patch.object(rbac_callback, "get_user_service", side_effect=RuntimeError("db down")):
            response = rbac_callback.rbac_before_model_callback(context, MagicMock())
        self.assertIsNotNone(response)
        self.assertEqual(response.error_code, "RBAC_ACCESS_DENIED")


class TestLangGraphHook(unittest.TestCase):

    def test_an_error_during_the_check_denies(self):
        from shared.utils.langgraph import hooks
        with patch("shared.utils.user_service.get_user_service", side_effect=RuntimeError("db down")):
            event = hooks.check_rbac("u", "some_bot")
        self.assertIsNotNone(event)
        self.assertIn("Access Denied", str(event))


if __name__ == "__main__":
    unittest.main()
