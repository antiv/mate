#!/usr/bin/env python3
"""
Tests for deny-by-default authorization on mutating /dashboard/api routes.

Dashboard pages checked is_admin_user but the JSON APIs behind them did not:
any authenticated user could POST /dashboard/api/users with roles=["admin"].
"""

import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.dashboard_authz import DashboardAuthzMiddleware, USER_WRITABLE_PATHS


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

    @app.post("/dashboard/api/workroom/title")
    async def workroom_title():
        return {"ok": True}

    @app.post("/run_sse")
    async def run_sse():
        return {"ok": True}

    return app


class TestDashboardAuthz(unittest.TestCase):
    """Writes require admin; reads and allowlisted routes do not."""

    def setUp(self):
        self.client = TestClient(_app())

    def _as(self, admin: bool):
        return patch("server.auth.is_admin_user", return_value=admin)

    def test_non_admin_cannot_create_user(self):
        with self._as(False):
            r = self.client.post("/dashboard/api/users")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Admin", r.json()["detail"])

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

    def test_reads_are_not_gated(self):
        with self._as(False):
            self.assertEqual(self.client.get("/dashboard/api/users").status_code, 200)

    def test_workroom_title_allowlisted_for_users(self):
        with self._as(False):
            self.assertEqual(self.client.post("/dashboard/api/workroom/title").status_code, 200)

    def test_non_dashboard_routes_untouched(self):
        with self._as(False):
            self.assertEqual(self.client.post("/run_sse").status_code, 200)

    def test_allowlist_stays_small(self):
        # Every entry here is a route a non-admin may write to; keep it deliberate.
        self.assertEqual(len(USER_WRITABLE_PATHS), 1)


if __name__ == "__main__":
    unittest.main()
