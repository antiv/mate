#!/usr/bin/env python3
"""
Tests that /dashboard/api/agents/import reports the real reason it failed.

The handler raised its own HTTPException inside a `try` whose `except Exception`
then swallowed and rewrapped it, so every import failure reached the dashboard
as "Invalid JSON data: 400: <real reason>" — naming the wrong cause for a body
that parsed perfectly well. Seventeen other handlers in the same file already
re-raise HTTPException first; this one was the outlier.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestImportAgentsErrorMessage(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.utils.dashboard.dashboard_server import DashboardServer

        with patch("shared.utils.database_client.get_database_client",
                   return_value=MagicMock()):
            project_root = Path(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
            app = FastAPI()
            self.server = DashboardServer(app, project_root)

        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "alice"
        self.client = TestClient(app)

    def test_a_rejected_import_reports_its_own_reason(self):
        self.server._import_agent_configs = MagicMock(
            return_value={"error": "Invalid import format: missing 'agents' array"})

        resp = self.client.post("/dashboard/api/agents/import", json={"nope": []})

        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertEqual(detail, "Invalid import format: missing 'agents' array")
        # The old rewrap blamed the request body for a failure that had nothing
        # to do with parsing it.
        self.assertNotIn("Invalid JSON data", detail)

    def test_a_genuinely_unparseable_body_still_says_so(self):
        # The except Exception branch still has a job — this is what it is for.
        resp = self.client.post(
            "/dashboard/api/agents/import",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid JSON data", resp.json()["detail"])

    def test_a_successful_import_is_unaffected(self):
        self.server._import_agent_configs = MagicMock(
            return_value={"success": True, "imported_count": 2, "skipped_count": 0, "errors": []})
        resp = self.client.post("/dashboard/api/agents/import", json={"agents": []})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["imported_count"], 2)


if __name__ == "__main__":
    unittest.main()
