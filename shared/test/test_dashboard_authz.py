#!/usr/bin/env python3
"""
Tests for deny-by-default authorization on mutating /dashboard/api routes.

Dashboard pages checked is_admin_user but the JSON APIs behind them did not:
any authenticated user could POST /dashboard/api/users with roles=["admin"].
"""

import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import PropertyMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.dashboard_authz import (
    DashboardAuthzMiddleware, USER_READABLE_PATHS, USER_WRITABLE_PATHS, is_platform_only,
)
from server.request_context import RequestContext


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DashboardAuthzMiddleware)

    @app.post("/dashboard/api/users")
    async def create_user():
        return {"ok": True}

    @app.put("/dashboard/api/agents/1")
    async def update_agent():
        return {"ok": True}

    @app.delete("/dashboard/api/agents/x/delete-folder")
    async def delete_folder():
        return {"ok": True}

    @app.get("/dashboard/api/users")
    async def list_users():
        return {"ok": True}

    @app.get("/dashboard/api/feedback")
    async def list_feedback():
        return {"ok": True}

    @app.get("/dashboard/api/tokens")
    async def list_tokens():
        return {"ok": True}

    @app.post("/dashboard/api/tokens")
    async def create_token():
        return {"ok": True}

    @app.delete("/dashboard/api/tokens/{token_id}")
    async def revoke_token(token_id: str):
        return {"ok": True}

    @app.post("/dashboard/api/feedback")
    async def submit_feedback():
        return {"ok": True}

    @app.post("/dashboard/api/workroom/title")
    async def workroom_title():
        return {"ok": True}

    @app.post("/run_sse")
    async def run_sse():
        return {"ok": True}

    return app


class TestDashboardAuthz(unittest.TestCase):
    """Everything requires admin, except the allowlisted routes."""

    def setUp(self):
        self.client = TestClient(_app())

    @contextmanager
    def _as(self, admin: bool, platform_admin: bool = None):
        """Signed in as an admin of the current workspace or not, and a platform
        administrator or not (by default, as admin)."""
        platform_admin = admin if platform_admin is None else platform_admin
        with ExitStack() as stack:
            stack.enter_context(patch("server.auth.get_dashboard_auth_user", return_value="someone"))
            stack.enter_context(patch.object(RequestContext, "is_admin", new_callable=PropertyMock,
                                             return_value=admin))
            stack.enter_context(patch.object(RequestContext, "is_platform_admin",
                                             new_callable=PropertyMock, return_value=platform_admin))
            stack.enter_context(patch("shared.utils.audit_service.log"))
            yield

    def test_non_admin_cannot_create_user(self):
        with self._as(False):
            r = self.client.post("/dashboard/api/users")
        self.assertEqual(r.status_code, 403)
        # Users are the installation's, so the platform's administrator's
        self.assertIn("Platform administrator", r.json()["detail"])

    def test_non_admin_cannot_write_agents(self):
        with self._as(False):
            self.assertEqual(self.client.put("/dashboard/api/agents/1").status_code, 403)
            self.assertEqual(
                self.client.delete("/dashboard/api/agents/x/delete-folder").status_code, 403
            )

    def test_admin_can_write(self):
        with self._as(True):
            self.assertEqual(self.client.post("/dashboard/api/users").status_code, 200)
            self.assertEqual(self.client.put("/dashboard/api/agents/1").status_code, 200)

    def test_non_admin_cannot_read(self):
        # The user list, audit log, conversations and widget admin keys were
        # readable by any signed-in user while only writes were gated
        with self._as(False):
            self.assertEqual(self.client.get("/dashboard/api/users").status_code, 403)

    def test_admin_can_read(self):
        with self._as(True):
            self.assertEqual(self.client.get("/dashboard/api/users").status_code, 200)

    def test_own_ratings_and_tokens_readable_for_users(self):
        with self._as(False):
            self.assertEqual(self.client.get("/dashboard/api/feedback").status_code, 200)
            self.assertEqual(self.client.get("/dashboard/api/tokens").status_code, 200)

    def test_workroom_title_allowlisted_for_users(self):
        with self._as(False):
            self.assertEqual(self.client.post("/dashboard/api/workroom/title").status_code, 200)

    def test_rating_a_reply_allowlisted_for_users(self):
        # The Work Room's thumbs and note were refused for non-admins, silently
        with self._as(False):
            self.assertEqual(self.client.post("/dashboard/api/feedback").status_code, 200)

    def test_own_tokens_writable_for_users(self):
        # The handler checks ALLOWED_API_ROLES before it creates one
        with self._as(False):
            self.assertEqual(self.client.post("/dashboard/api/tokens").status_code, 200)
            self.assertEqual(self.client.delete("/dashboard/api/tokens/5").status_code, 200)
            self.assertEqual(self.client.delete("/dashboard/api/tokens/5/x").status_code, 403)

    def test_a_workspace_admin_reaches_the_workspace_but_not_the_installation(self):
        with self._as(True, platform_admin=False):
            self.assertEqual(self.client.put("/dashboard/api/agents/1").status_code, 200)
            for response in (self.client.get("/dashboard/api/users"),
                             self.client.post("/dashboard/api/users")):
                self.assertEqual(response.status_code, 403)
                self.assertIn("Platform administrator", response.json()["detail"])

    def test_a_workspace_the_caller_cannot_open_is_a_404(self):
        with self._as(False), patch.object(
                RequestContext, "is_admin", new_callable=PropertyMock,
                side_effect=HTTPException(status_code=404, detail="Workspace not found")):
            self.assertEqual(self.client.put("/dashboard/api/agents/1").status_code, 404)

    def test_nobody_signed_in_is_left_to_the_route(self):
        # The route's own dependency answers 401 or redirects to the login page
        with patch("server.auth.get_dashboard_auth_user", return_value=None):
            self.assertEqual(self.client.put("/dashboard/api/agents/1").status_code, 200)

    def test_platform_only_paths(self):
        for method, path in (("GET", "/dashboard/api/users"), ("PUT", "/dashboard/api/users/u"),
                             ("GET", "/dashboard/api/settings/model-prices"),
                             ("POST", "/dashboard/api/agents/build-binary"),
                             ("DELETE", "/dashboard/api/templates/t")):
            self.assertTrue(is_platform_only(method, path), f"{method} {path}")
        for method, path in (("GET", "/dashboard/api/usage/logs"), ("GET", "/dashboard/api/templates/t"),
                             ("POST", "/dashboard/api/agents"), ("GET", "/dashboard/api/userstats")):
            self.assertFalse(is_platform_only(method, path), f"{method} {path}")

    def test_non_dashboard_routes_untouched(self):
        with self._as(False):
            self.assertEqual(self.client.post("/run_sse").status_code, 200)

    def test_allowlist_stays_small(self):
        # Every entry here is a route a non-admin may write to; keep it deliberate.
        self.assertEqual(len(USER_WRITABLE_PATHS), 7)
        self.assertEqual(len(USER_READABLE_PATHS), 4)


if __name__ == "__main__":
    unittest.main()
