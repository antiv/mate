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
import socket
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

    def test_the_camel_case_user_id_counts_too(self):
        # ADK runs as userId when a body has both spellings, so a body showing the
        # check its own user_id and ADK another userId ran as that other user
        def refused(body):
            return _non_admin_refusal("run_sse", "bob@corp.com", json.dumps(body).encode())

        self.assertIsNotNone(refused({"app_name": "bot", "user_id": "bob@corp.com", "userId": "admin"}))
        self.assertIsNotNone(refused({"app_name": "bot", "userId": "admin"}))
        self.assertIsNone(refused({"appName": "bot", "userId": "bob@corp.com"}))
        self.assertIsNone(refused({"app_name": "bot", "user_id": "bob@corp.com", "userId": "bob@corp.com"}))
        self.assertIsNotNone(_non_admin_refusal("run_sse", "bob@corp.com", b'["bob@corp.com"]'))

    def test_what_passes_is_what_adk_runs_as(self):
        from google.adk.cli.adk_web_server import RunAgentRequest
        message = {"role": "user", "parts": [{"text": "hi"}]}
        bodies = [
            {"app_name": "bot", "user_id": "bob@corp.com", "session_id": "s", "new_message": message},
            {"appName": "bot", "userId": "bob@corp.com", "sessionId": "s", "newMessage": message},
            {"app_name": "bot", "user_id": "bob@corp.com", "userId": "admin", "session_id": "s",
             "new_message": message},
            {"app_name": "bot", "user_id": "admin", "userId": "bob@corp.com", "session_id": "s",
             "new_message": message},
        ]
        for body in bodies:
            with self.subTest(body=body):
                if _non_admin_refusal("run_sse", "bob@corp.com", json.dumps(body).encode()) is None:
                    self.assertEqual(RunAgentRequest.model_validate(body).user_id, "bob@corp.com")

    def test_dot_segments_are_refused(self):
        # httpx resolves them before forwarding, so the path checked is not the
        # path the agent server serves
        for path in ("apps/bot/users/bob@corp.com/../alice@corp.com/sessions",
                     "apps/bot/users/bob@corp.com/sessions/../../alice@corp.com/sessions",
                     "apps/bot/users/bob@corp.com/../../../../run_sse",
                     "apps/bot/users/bob@corp.com/./sessions",
                     "apps/bot/users/bob@corp.com/%2e%2e/alice@corp.com/sessions",
                     "apps/bot/users/bob@corp.com/%2E./alice@corp.com/sessions",
                     "apps/bot/users/bob@corp.com/..%2falice@corp.com/sessions",
                     "a2a/bot/../../apps/other/users/alice@corp.com/sessions"):
            with self.subTest(path=path):
                self.assertIsNotNone(_non_admin_refusal(path, "bob@corp.com", None))

    def test_dots_inside_a_segment_still_pass(self):
        for path in ("apps/bot/users/bob@corp.com/sessions/s1/artifacts/report.v2.pdf/versions/0",
                     "apps/bot/users/bob@corp.com/sessions/s1/artifacts/..hidden/versions/0"):
            with self.subTest(path=path):
                self.assertIsNone(_non_admin_refusal(path, "bob@corp.com", None))

    def test_the_rest_of_the_agent_server_is_admin_only(self):
        self.assertIsNone(_non_admin_refusal("list-apps", "bob@corp.com", None))
        for path in ("debug/trace/e1", "apps/bot/eval_sets", "dev/build_graph/bot", "builder/save",
                     "dev-ui", "dev-ui/", "dev-ui/main.js"):
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

    def test_a_non_admin_cannot_run_as_someone_else_with_the_camel_case_id(self):
        resp = self.client.post("/run_sse", json={"app_name": "bot", "user_id": "bob@corp.com",
                                                  "userId": "admin", "session_id": "s",
                                                  "new_message": {"parts": []}})
        self.assertEqual(resp.status_code, 403)

    def test_a_non_admin_cannot_open_the_dev_ui(self):
        resp = self.client.get("/dev-ui/?app=bot")
        self.assertEqual(resp.status_code, 403)

    def test_a_non_admin_cannot_climb_out_of_their_own_path(self):
        # httpx would resolve a literal ../ in the test client too; the server
        # decodes %2e%2e to .. just as it keeps a raw ../ from curl --path-as-is
        resp = self.client.get("/apps/bot/users/bob@corp.com/%2e%2e/alice@corp.com/sessions")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/apps/bot/users/bob@corp.com/%2e%2e/%2e%2e/%2e%2e/%2e%2e/run_sse",
                                json={"app_name": "bot", "user_id": "alice@corp.com",
                                      "session_id": "s", "new_message": {"parts": []}})
        self.assertEqual(resp.status_code, 403)

    def test_a_non_admin_still_reaches_their_own_sessions(self):
        from server import proxy_routes
        # Passes the check and fails only on reaching the agent server, which is
        # absent. The workspace check has its own tests (test_cross_workspace.py).
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        with patch.object(proxy_routes, "ADK_HOST", "127.0.0.1"), \
                patch.object(proxy_routes, "ADK_PORT", free_port), \
                patch.object(proxy_routes, "_workspace_refusal", return_value=None):
            resp = self.client.get("/apps/bot/users/bob@corp.com/sessions")
        self.assertEqual(resp.status_code, 503)

    def test_an_admin_is_not_restricted(self):
        from server import proxy_routes
        self.admin = True
        # Passes the check and fails only on reaching the agent server, which is
        # absent: a port nothing listens on, even with a local server running
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        with patch.object(proxy_routes, "ADK_HOST", "127.0.0.1"), \
                patch.object(proxy_routes, "ADK_PORT", free_port):
            resp = self.client.get("/apps/bot/users/alice@corp.com/sessions")
        self.assertEqual(resp.status_code, 503)


class TestRunLiveQuery(unittest.TestCase):
    """The /run_live proxy builds the agent server's URL from the query it got."""

    def _backend_url(self, session_id):
        from server import proxy_routes
        app = FastAPI()
        app.include_router(proxy_routes.router)
        seen = []

        def connect(url, *args, **kwargs):
            seen.append(url)
            raise ConnectionError("no agent server in this test")

        with patch("server.proxy_routes.verify_token", return_value=True), \
                patch("server.proxy_routes.websockets.connect", side_effect=connect):
            client = TestClient(app)
            try:
                with client.websocket_connect("/run_live", params={
                        "app_name": "bot", "user_id": "bob@corp.com", "session_id": session_id,
                        "token": "t"}) as ws:
                    ws.receive_text()
            except Exception:
                pass
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_the_session_id_cannot_add_a_second_user_id(self):
        from urllib.parse import parse_qs, urlsplit
        url = self._backend_url("s1&user_id=alice@corp.com")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["user_id"], ["bob@corp.com"])
        self.assertEqual(query["session_id"], ["s1&user_id=alice@corp.com"])

    def test_a_plain_session_id_reaches_the_agent_server_unchanged(self):
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(self._backend_url("4f9c-11ab_x")).query)
        self.assertEqual((query["app_name"], query["user_id"], query["session_id"]),
                         (["bot"], ["bob@corp.com"], ["4f9c-11ab_x"]))


class TestWidgetSessionUrl(unittest.TestCase):
    """The widget creates sessions under a visitor id its caller chose."""

    def _created_at(self, user_id):
        import asyncio
        from server import widget_routes
        seen = []

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"id": "s1"}

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, **kwargs):
                seen.append(url)
                return Response()

        with patch("server.widget_routes.httpx.AsyncClient", Client):
            self.assertEqual(asyncio.run(widget_routes._create_adk_session("bot", user_id)), "s1")
        return seen[0]

    def test_a_visitor_id_cannot_climb_to_another_agent(self):
        import httpx
        url = self._created_at("widget_1_x/../../../other/users/alice@corp.com")
        segments = httpx.URL(url).raw_path.decode().split("/")
        self.assertNotIn("..", segments)
        self.assertEqual(segments[:3], ["", "apps", "bot"])

    def test_a_generated_visitor_id_is_unchanged(self):
        url = self._created_at("widget_1_k3j9x2abc")
        self.assertTrue(url.endswith("/apps/bot/users/widget_1_k3j9x2abc/sessions"), url)


if __name__ == "__main__":
    unittest.main()
