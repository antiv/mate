#!/usr/bin/env python3
"""
Personal access tokens from the account menu.

Only an admin could create one: the authz middleware refused POST
/dashboard/api/tokens to everyone else, although the API accepts a token of any
user with a role in ALLOWED_API_ROLES (default admin, developer). Users with such
a role now create and revoke their own tokens; the menu offers Personal Tokens
only to them.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.dashboard.dashboard_server import DashboardServer
from shared.test.request_context_helpers import as_caller


class TestDashboardTokens(unittest.TestCase):

    def setUp(self):
        db = MagicMock()
        p = patch("shared.utils.database_client.get_database_client", return_value=db)
        p.start()
        self.addCleanup(p.stop)
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test")
        self.server = DashboardServer(self.app, Path(__file__).resolve().parent.parent.parent)
        self.server.db_client = db
        self.server._get_all_agent_configs = lambda: []
        self.client = TestClient(self.app)

    def _as(self, *, admin: bool = False, roles=("user",)):
        """Patch the caller to be alice with these roles."""
        service = MagicMock()
        service.get_user_roles.return_value = list(roles)
        patches = [
            patch("server.auth.get_dashboard_auth_user", return_value="alice"),
            patch("server.auth.is_admin_user", return_value=admin),
            patch("shared.utils.user_service.get_user_service", return_value=service),
            patch.dict(os.environ, {"ALLOWED_API_ROLES": "developer"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        as_caller(self.app, "alice", admin)

    def test_user_without_an_api_role_cannot_create_a_token(self):
        self._as(roles=["user"])
        r = self.client.post("/dashboard/api/tokens", json={"name": "ci"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("API access", r.json()["detail"])
        self.server.db_client.get_session.assert_not_called()

    def test_user_with_an_api_role_creates_a_token(self):
        self._as(roles=["developer"])
        with patch("shared.utils.dashboard.dashboard_server.audit_service.log"):
            r = self.client.post("/dashboard/api/tokens", json={"name": "ci", "expires_in_days": 30})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["token"].startswith("mate_pat_"))
        pat = self.server.db_client.get_session.return_value.add.call_args[0][0]
        self.assertEqual(pat.user_id, "alice")
        self.assertIsNotNone(pat.expires_at)

    def test_invalid_expiry_is_a_400(self):
        self._as(roles=["developer"])
        for value in ["soon", -1, "nan", 1e12]:
            with self.subTest(value=value):
                r = self.client.post("/dashboard/api/tokens", json={"expires_in_days": value})
                self.assertEqual(r.status_code, 400)

    def test_menu_offers_tokens_to_users_with_an_api_role(self):
        self._as(roles=["developer"])
        self.assertIn("Personal Tokens", self.client.get("/dashboard/workroom").text)

    def test_menu_hides_tokens_from_other_users(self):
        self._as(roles=["user"])
        page = self.client.get("/dashboard/workroom").text
        self.assertIn("Logout", page)
        self.assertNotIn("Personal Tokens", page)

    def test_menu_offers_tokens_to_admins(self):
        self._as(admin=True, roles=[])
        self.assertIn("Personal Tokens", self.client.get("/dashboard/workroom").text)


if __name__ == "__main__":
    unittest.main()
