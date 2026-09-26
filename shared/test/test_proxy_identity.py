#!/usr/bin/env python3
"""
Unit tests for the user id the agent-server proxy lets through.

ADK takes the user id from the URL and the request body, and RBAC, session
history and the user profile all key on it. The proxy forwarded whatever the
browser sent, so a signed-in user who was not an admin could read another
user's conversations, or run as an admin and get past RBAC.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server.proxy_routes import _non_admin_refusal


class TestNonAdminRefusal(unittest.TestCase):

    def test_own_sessions_pass(self):
        for path in ("apps/bot/users/bob@corp.com/sessions",
                     "apps/bot/users/bob@corp.com/sessions/s1",
                     "apps/bot/users/bob@corp.com/sessions/s1/artifacts/a.png/versions/0"):
            with self.subTest(path=path):
                self.assertIsNone(_non_admin_refusal(path, "bob@corp.com", None))

    def test_someone_elses_sessions_are_refused(self):
        self.assertIsNotNone(_non_admin_refusal("apps/bot/users/alice@corp.com/sessions",
                                                "bob@corp.com", None))

    def test_a_run_must_carry_the_users_own_id(self):
        own = json.dumps({"app_name": "bot", "user_id": "bob@corp.com"}).encode()
        other = json.dumps({"app_name": "bot", "user_id": "alice@corp.com"}).encode()
        self.assertIsNone(_non_admin_refusal("run_sse", "bob@corp.com", own))
        self.assertIsNone(_non_admin_refusal("run", "bob@corp.com", own))
        self.assertIsNotNone(_non_admin_refusal("run_sse", "bob@corp.com", other))
        self.assertIsNotNone(_non_admin_refusal("run_sse", "bob@corp.com", b"not json"))
        self.assertIsNotNone(_non_admin_refusal("run_sse", "bob@corp.com", None))

    def test_the_rest_of_the_agent_server_is_admin_only(self):
        self.assertIsNone(_non_admin_refusal("list-apps", "bob@corp.com", None))
        for path in ("debug/trace/e1", "apps/bot/eval_sets", "dev/build_graph/bot", "builder/save"):
            with self.subTest(path=path):
                self.assertIsNotNone(_non_admin_refusal(path, "bob@corp.com", None))


class TestProxyRoute(unittest.TestCase):
    """The route refuses before contacting the agent server."""

    def setUp(self):
        from server import proxy_routes
        app = FastAPI()
        app.include_router(proxy_routes.router)
        self.admin = False
        app.dependency_overrides[proxy_routes.get_auth_user] = lambda: "bob@corp.com"
        patcher = patch("server.auth.is_admin_user", side_effect=lambda request: self.admin)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def test_a_non_admin_cannot_list_another_users_sessions(self):
        resp = self.client.get("/apps/bot/users/alice@corp.com/sessions")
        self.assertEqual(resp.status_code, 403)

    def test_a_non_admin_cannot_run_as_someone_else(self):
        resp = self.client.post("/run_sse", json={"app_name": "bot", "user_id": "admin",
                                                  "session_id": "s", "new_message": {"parts": []}})
        self.assertEqual(resp.status_code, 403)

    def test_an_admin_is_not_restricted(self):
        self.admin = True
        # Passes the check and fails only on reaching the (absent) agent server
        resp = self.client.get("/apps/bot/users/alice@corp.com/sessions")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
