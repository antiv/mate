#!/usr/bin/env python3
"""
Work Room empty-state "Create your first agent" must only appear for admins.

Non-admins are redirected from /dashboard/agents back to /dashboard/workroom,
so offering them that link creates a redirect loop (#239).
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


class TestWorkroomCreateAgentLink(unittest.TestCase):
    def setUp(self):
        db = MagicMock()
        p = patch(
            "shared.utils.database_client.get_database_client",
            return_value=db,
        )
        p.start()
        self.addCleanup(p.stop)
        root = Path(__file__).resolve().parent.parent.parent
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test")
        self.server = DashboardServer(self.app, root)
        self.server.db_client = db
        self.server._get_all_agent_configs = lambda: []

    def _get_workroom(self, *, is_admin: bool) -> str:
        as_caller(self.app, "admin" if is_admin else "alice", is_admin)
        resp = TestClient(self.app).get("/dashboard/workroom")
        self.assertEqual(resp.status_code, 200)
        return resp.text

    def test_admin_sees_create_first_agent_link(self):
        page = self._get_workroom(is_admin=True)
        self.assertIn("No agents configured yet.", page)
        self.assertIn('href="/dashboard/agents"', page)
        self.assertIn("Create your first agent", page)
        self.assertNotIn("Ask an administrator to add an agent.", page)

    def test_non_admin_sees_ask_admin_not_create_link(self):
        page = self._get_workroom(is_admin=False)
        self.assertIn("No agents configured yet.", page)
        self.assertIn("Ask an administrator to add an agent.", page)
        self.assertNotIn("Create your first agent", page)
        self.assertNotIn('href="/dashboard/agents"', page)

    def test_template_gates_create_link_on_is_admin(self):
        template = (
            Path(__file__).resolve().parent.parent.parent
            / "templates"
            / "dashboard"
            / "workroom.html"
        ).read_text(encoding="utf-8")
        # Empty-state create link must sit inside {% if is_admin %}
        empty = template.split("No agents configured yet.", 1)[1].split("{% endif %}", 1)[0]
        self.assertIn("{% if is_admin %}", empty)
        self.assertIn('href="/dashboard/agents"', empty)
        self.assertIn("Ask an administrator to add an agent.", empty)


if __name__ == "__main__":
    unittest.main()
