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


class TestMissingContextDenies(_Db):
    """The callback used to let a model call through when it could not tell who was
    asking, which agent it was, or find the agent's config (also when the lookup
    failed). None of these grants access now."""

    def _call(self, context, manager):
        from shared.callbacks import rbac_callback
        users = self._user_service()
        with patch.object(rbac_callback, "get_user_service", return_value=users), \
                patch("shared.utils.agent_manager.get_agent_manager", return_value=manager), \
                patch.object(rbac_callback, "_log_access_denied_event"):
            return rbac_callback.rbac_before_model_callback(context, MagicMock())

    def _manager(self, row=None):
        manager = MagicMock()
        manager.get_root_agent_by_name.return_value = row
        manager.get_session.return_value = None
        return manager

    def assertDenied(self, response):
        self.assertIsNotNone(response)
        self.assertEqual(response.error_code, "RBAC_ACCESS_DENIED")

    def test_no_user_id_is_denied(self):
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id=None),
                                  agent_name="user_bot")
        self.assertDenied(self._call(context, self._manager()))

    def test_no_agent_name_is_denied(self):
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="u"),
                                  agent_name=None, agent=None)
        self.assertDenied(self._call(context, self._manager()))

    def test_an_agent_without_a_config_is_denied(self):
        # get_root_agent_by_name returns None for a missing or disabled row, and also
        # when the query raised, so this covers a database error too
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="u"),
                                  agent_name="gone_bot")
        self.assertDenied(self._call(context, self._manager(row=None)))

    def test_skipped_agents_still_run_without_a_config(self):
        # Refusing on a missing config must not catch what the check skips
        from shared.utils import eval_agent_runner
        fallback = _fallback_agent()
        system = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="u", agent=fallback),
                                 agent_name=fallback.name)
        self.assertIsNone(self._call(system, self._manager(row=None)))
        token = eval_agent_runner._EVAL_RUN.set(True)
        try:
            evaluated = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="eval_runner"),
                                        agent_name="snapshot_bot")
            self.assertIsNone(self._call(evaluated, self._manager(row=None)))
        finally:
            eval_agent_runner._EVAL_RUN.reset(token)


def _fallback_agent():
    from shared.utils import fallback_agent
    with patch.object(fallback_agent, "create_model", return_value="gemini-2.5-flash"):
        return fallback_agent.create_fallback_agent()


class TestSkipIsByObject(_Db):
    """The check skipped any agent named mate_fallback_agent, system_agent or
    health_check_agent, so an agents_config row given one of those names (the
    dashboard and the create_agent tool accept any name) ran with no role check.
    Only the agent create_fallback_agent built is skipped now."""

    def _run(self, agent_obj, name, row):
        from shared.callbacks import rbac_callback
        from shared.utils.rbac_middleware import RBACMiddleware
        users = self._user_service()
        with patch("shared.utils.rbac_middleware.get_user_service", return_value=users):
            middleware = RBACMiddleware()
        manager = MagicMock()
        manager.get_root_agent_by_name.return_value = row
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="plain_user", agent=agent_obj),
                                  agent_name=name)
        with patch.object(rbac_callback, "get_user_service", return_value=users), \
                patch.object(rbac_callback, "get_rbac_middleware", return_value=middleware), \
                patch("shared.utils.agent_manager.get_agent_manager", return_value=manager), \
                patch.object(rbac_callback, "_log_access_denied_event"):
            return rbac_callback.rbac_before_model_callback(context, MagicMock())

    def test_a_configured_agent_with_a_reserved_name_is_checked(self):
        for name in ("mate_fallback_agent", "system_agent", "health_check_agent"):
            admin_only = AgentConfig(name=name, allowed_for_roles=None)
            response = self._run(SimpleNamespace(name=name), name, admin_only)
            self.assertIsNotNone(response, name)
            self.assertEqual(response.error_code, "RBAC_ACCESS_DENIED")

    def test_the_fallback_agent_is_skipped(self):
        fallback = _fallback_agent()
        self.assertIsNone(self._run(fallback, fallback.name, None))


class TestCallbackChain(unittest.TestCase):

    def test_a_check_that_fails_to_run_denies(self):
        from shared.callbacks import user_profile_callback
        context = SimpleNamespace(agent_name="some_bot")
        with patch("shared.callbacks.token_usage_callback.capture_model_name_callback"), \
                patch.object(user_profile_callback, "user_profile_context_callback"), \
                patch("shared.callbacks.rbac_callback.rbac_before_model_callback",
                      side_effect=RuntimeError("broken")), \
                patch("shared.callbacks.guardrail_callback.guardrail_before_model_callback",
                      return_value=None):
            response = user_profile_callback.combined_user_profile_and_rbac_callback(context, MagicMock())
        self.assertIsNotNone(response)
        self.assertEqual(response.error_code, "RBAC_ACCESS_DENIED")


class TestLangGraphHook(unittest.TestCase):

    def test_an_error_during_the_check_denies(self):
        from shared.utils.langgraph import hooks
        with patch("shared.utils.user_service.get_user_service", side_effect=RuntimeError("db down")):
            event = hooks.check_rbac("u", "some_bot")
        self.assertIsNotNone(event)
        self.assertIn("Access Denied", str(event))

    def test_an_agent_without_a_config_is_denied(self):
        from shared.utils.langgraph import hooks
        user = MagicMock()
        user.get_roles.return_value = ["user"]
        with patch("shared.utils.user_service.get_user_service") as users, \
                patch.object(hooks, "_load_agent_config_row", return_value=None):
            users.return_value.get_or_create_user.return_value = user
            event = hooks.check_rbac("u", "gone_bot")
        self.assertIsNotNone(event)
        self.assertEqual(event["error_code"], "RBAC_ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main()
