#!/usr/bin/env python3
"""
Unit tests for who may read an artifact through /api/widget/artifacts.

The route needed no login. Its path went into the agent-server URL as given,
dot segments included, and a fallback route globbed the artifacts folder for
the requested name, so /api/widget/artifacts/*.txt returned the newest text
file of any user.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

KEY = SimpleNamespace(id=7, agent_name="bot")


class TestArtifactAccess(unittest.TestCase):

    def setUp(self):
        from server import widget_routes
        app = FastAPI()
        app.include_router(widget_routes.public_artifacts_router)
        self.client = TestClient(app)
        self.requested = []

        async def fake_send(client, request, stream=False):
            self.requested.append(str(request.url))
            return httpx.Response(200, json={"inlineData": {"data": "", "mimeType": "image/png"}})

        self.identity, self.admin = None, False
        patches = [
            patch.object(widget_routes, "_lookup_widget_key",
                         side_effect=lambda k: KEY if k == "wk_good" else None),
            patch("server.auth.get_dashboard_auth_user", side_effect=lambda r: self.identity),
            patch("server.auth.is_admin_user", side_effect=lambda r: self.admin),
            patch.object(httpx.AsyncClient, "send", fake_send),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _get(self, path, key=None):
        headers = {"X-Widget-Key": key} if key else {}
        return self.client.get("/api/widget/artifacts/" + path, headers=headers)

    def test_anonymous_is_refused(self):
        self.assertEqual(self._get("bot/alice/s1/a.png/0").status_code, 401)
        self.assertEqual(self.requested, [])

    def test_the_filename_glob_route_is_gone(self):
        self.assertEqual(self._get("*.txt").status_code, 404)

    def test_a_widget_reads_its_own_visitors_scoped(self):
        self.assertEqual(self._get("bot/uid123/s1/a.png/0", key="wk_good").status_code, 200)
        self.assertEqual(self._get("bot/widget_7_uid123/s1/a.png/0", key="wk_good").status_code, 200)
        self.assertTrue(all("/users/widget_7_uid123/" in u for u in self.requested))

    def test_a_widget_cannot_reach_other_users_or_agents(self):
        self.assertEqual(self._get("other_bot/uid/s1/a.png/0", key="wk_good").status_code, 403)
        self.assertEqual(self._get("bot/uid/s1/a.png/0", key="wk_bad").status_code, 403)
        self._get("bot/alice@corp.com/s1/a.png/0", key="wk_good")
        self.assertIn("/users/widget_7_alice%40corp.com/", self.requested[-1])

    def test_a_non_admin_reads_only_their_own(self):
        self.identity = "bob@corp.com"
        self.assertEqual(self._get("bot/alice@corp.com/s1/a.png/0").status_code, 403)
        self.assertEqual(self._get("bot/bob@corp.com/s1/a.png/0").status_code, 200)

    def test_an_admin_reads_any(self):
        self.identity, self.admin = "admin", True
        self.assertEqual(self._get("bot/alice@corp.com/s1/a.png/0").status_code, 200)

    def test_dot_segments_are_refused(self):
        self.identity, self.admin = "admin", True
        self.assertEqual(self._get("bot/alice/s1/%2E%2E/%2E%2E").status_code, 400)
        self.assertEqual(self.requested, [])


if __name__ == "__main__":
    unittest.main()
