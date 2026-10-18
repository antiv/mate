#!/usr/bin/env python3
"""
The Documentation page API tab must embed agent Swagger/ReDoc via same-origin
/docs and /redoc (proxied to ADK_PORT). Hard-coding dashboard port 8000 breaks
when the dashboard is published on another port (e.g. Docker -p 9000:8000).
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


class TestDocsApiSameOrigin(unittest.TestCase):
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

    def _get_docs_page(self) -> str:
        self.app.dependency_overrides[self.server._get_auth_user_dependency] = (
            lambda: "admin"
        )
        with patch.object(self.server, "_get_is_admin", return_value=True):
            resp = TestClient(self.app).get("/dashboard/docs")
        self.assertEqual(resp.status_code, 200)
        return resp.text

    def test_docs_page_does_not_hard_code_port_8000_for_agent_api(self):
        page = self._get_docs_page()
        self.assertNotIn('"adk_port": 8000', page)
        self.assertNotIn("adk_port", page)
        # Template must not inject a numeric agent port into JS
        self.assertNotIn("const adkPort =", page)
        self.assertNotIn("adkBaseUrl", page)

    def test_docs_page_loads_agent_api_from_same_origin_proxy(self):
        page = self._get_docs_page()
        self.assertIn("sameOriginBase", page)
        self.assertIn("${sameOriginBase}/docs", page)
        self.assertIn("${sameOriginBase}/redoc", page)
        self.assertIn("Agent Server", page)


if __name__ == "__main__":
    unittest.main()
