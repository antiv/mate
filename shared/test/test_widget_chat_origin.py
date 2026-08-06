#!/usr/bin/env python3
"""
Tests for the widget key origin allowlist on the chat surface.

The allowlist used to guard only the admin routes. The chat page and the
loader's public-config call are the two places where the embedding site is
actually visible to the server — once the iframe is loaded, its API calls are
same-origin and carry MATE's own origin, so they cannot be attributed to the
parent site. Both are now checked.
"""

import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import widget_routes as wr


class _Key:
    def __init__(self, origins=None):
        self.id = 1
        self.api_key = "wk_public"
        self.admin_key = "wak_secret"
        self.agent_name = "test_agent"
        self.project_id = 1
        self._origins = origins

    def get_allowed_origins(self):
        return self._origins

    def get_widget_config(self):
        return {"greeting": "hi", "theme": "light", "button_color": "#2563eb"}


def _client(origins):
    app = FastAPI()
    app.include_router(wr.router)
    patcher = patch.object(
        wr, "_lookup_widget_key",
        side_effect=lambda k: _Key(origins) if k == "wk_public" else None,
    )
    patcher.start()
    return TestClient(app, base_url="http://mate.local"), patcher


class TestPublicConfigOrigin(unittest.TestCase):
    """The loader's cross-origin call carries a browser-set Origin header."""

    def setUp(self):
        self.client, self.patcher = _client(["https://antonijevic.rs"])
        self.addCleanup(self.patcher.stop)

    def test_allowed_origin_passes(self):
        r = self.client.get("/widget/public-config?key=wk_public",
                            headers={"Origin": "https://antonijevic.rs"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["access-control-allow-origin"], "*")

    def test_foreign_origin_rejected_when_strict(self):
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/public-config?key=wk_public",
                                headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_lookalike_origin_rejected_when_strict(self):
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/public-config?key=wk_public",
                                headers={"Origin": "https://antonijevic.rs.evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_foreign_origin_only_logged_when_not_strict(self):
        with patch.object(wr, "ORIGIN_STRICT", False):
            r = self.client.get("/widget/public-config?key=wk_public",
                                headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 200)

    def test_invalid_key_still_401(self):
        r = self.client.get("/widget/public-config?key=nope",
                            headers={"Origin": "https://antonijevic.rs"})
        self.assertEqual(r.status_code, 401)


class TestChatPageOrigin(unittest.TestCase):
    """The iframe navigation carries the parent page as Referer."""

    def setUp(self):
        self.client, self.patcher = _client(["https://antonijevic.rs"])
        self.addCleanup(self.patcher.stop)

    def test_allowed_parent_passes(self):
        r = self.client.get("/widget/chat?key=wk_public",
                            headers={"Referer": "https://antonijevic.rs/kontakt"})
        self.assertEqual(r.status_code, 200)

    def test_foreign_parent_rejected_when_strict(self):
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/chat?key=wk_public",
                                headers={"Referer": "https://evil.example/steal"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("not enabled for this site", r.text)

    def test_foreign_parent_only_logged_when_not_strict(self):
        with patch.object(wr, "ORIGIN_STRICT", False):
            r = self.client.get("/widget/chat?key=wk_public",
                                headers={"Referer": "https://evil.example/steal"})
        self.assertEqual(r.status_code, 200)

    def test_missing_referer_is_allowed(self):
        # Sites with a strict referrer policy must not break their own widget.
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/chat?key=wk_public")
        self.assertEqual(r.status_code, 200)

    def test_own_origin_always_allowed(self):
        # Dashboard preview and the wizard test chat embed this page themselves.
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/chat?key=wk_public",
                                headers={"Referer": "http://mate.local/widget/admin"})
        self.assertEqual(r.status_code, 200)

    def test_invalid_key_still_401(self):
        r = self.client.get("/widget/chat?key=nope")
        self.assertEqual(r.status_code, 401)


class TestNoAllowlistIsOpen(unittest.TestCase):
    """A key without an allowlist keeps working from anywhere."""

    def setUp(self):
        self.client, self.patcher = _client(None)
        self.addCleanup(self.patcher.stop)

    def test_chat_page_open(self):
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/chat?key=wk_public",
                                headers={"Referer": "https://anywhere.example/"})
        self.assertEqual(r.status_code, 200)

    def test_public_config_open(self):
        with patch.object(wr, "ORIGIN_STRICT", True):
            r = self.client.get("/widget/public-config?key=wk_public",
                                headers={"Origin": "https://anywhere.example"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
