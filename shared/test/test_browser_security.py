#!/usr/bin/env python3
"""
Unit tests for the server-side browser's boundaries.

The interactive browser websocket accepted anyone, and let the caller pick whose
browser to drive through a query parameter. The browser itself could load any
address the server can reach, internal ones included, whether steered by that
socket or by an agent's browser tool. And a widget visitor's client-chosen id
went into the profile directory path.
"""

import asyncio
import base64
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import itsdangerous
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.tools.browser_tools import _profile_dir_name, url_block_reason


def _blocked(url):
    return asyncio.run(url_block_reason(url))


class TestUrlGuard(unittest.TestCase):

    def test_internal_addresses_are_blocked(self):
        for url in ("http://127.0.0.1:8001/list-apps", "http://localhost/", "http://10.0.0.5/",
                    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
                    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/"):
            with self.subTest(url=url):
                self.assertIsNotNone(_blocked(url))

    def test_other_schemes_are_blocked(self):
        self.assertIsNotNone(_blocked("file:///etc/passwd"))
        self.assertIsNotNone(_blocked("ftp://example.com/"))

    def test_public_addresses_and_page_internals_pass(self):
        self.assertIsNone(_blocked("https://8.8.8.8/"))
        self.assertIsNone(_blocked("data:text/html,hi"))
        self.assertIsNone(_blocked("about:blank"))

    def test_private_networks_can_be_allowed_explicitly(self):
        with patch.dict(os.environ, {"BROWSER_ALLOW_PRIVATE_NETWORK": "true"}):
            self.assertIsNone(_blocked("http://127.0.0.1:8001/"))
            self.assertIsNotNone(_blocked("file:///etc/passwd"))


class TestProfileDir(unittest.TestCase):

    def test_a_plain_id_keeps_its_directory(self):
        self.assertEqual(_profile_dir_name("alice@corp.com"), "user_alice@corp.com")
        self.assertEqual(_profile_dir_name("widget_3_abc-123"), "user_widget_3_abc-123")

    def test_a_path_like_id_is_hashed(self):
        for user_id in ("widget_1_../../../etc", "a/b", "..", ""):
            with self.subTest(user_id=user_id):
                name = _profile_dir_name(user_id)
                self.assertNotIn("/", name)
                self.assertNotIn("..", name)


SECRET = "test-secret"


def _session_cookie(user):
    signer = itsdangerous.TimestampSigner(SECRET)
    return signer.sign(base64.b64encode(json.dumps({"user": user}).encode())).decode()


class TestInteractiveSocket(unittest.TestCase):

    def setUp(self):
        from server.browser_routes import router
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(SessionMiddleware, secret_key=SECRET)
        self.client = TestClient(app)
        patcher = patch("server.browser_routes.browser_manager.get_session",
                        new_callable=AsyncMock, side_effect=RuntimeError("stop here"))
        self.get_session = patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_anonymous_connection_is_refused(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/browser/interactive?user_id=victim") as ws:
                ws.receive_json()
        self.get_session.assert_not_called()

    def test_a_signed_in_user_gets_only_their_own_browser(self):
        self.client.cookies.set("session", _session_cookie({"user_id": "bob@corp.com", "provider": "google"}))
        with self.client.websocket_connect("/api/browser/interactive?user_id=alice@corp.com&session_id=s") as ws:
            message = ws.receive_json()
        self.assertEqual(message["type"], "error")
        self.assertEqual(self.get_session.call_args.args[0], "bob@corp.com")


if __name__ == "__main__":
    unittest.main()
