#!/usr/bin/env python3
"""
Tests for the widget key split and origin checking.

The embeddable api_key is public (it sits in the customer's page source), so it
must not authorise the /widget/api admin routes — reading the agent config,
rewriting instruction/model, memory blocks, or file uploads. Those need the
separate admin_key. Origin matching must also compare whole hosts, not prefixes.
"""

import unittest
from unittest.mock import patch
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import HTTPException

from server import widget_routes as wr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Key:
    def __init__(self, api_key="wk_public", admin_key="wak_secret", origins=None, id=1):
        self.api_key = api_key
        self.admin_key = admin_key
        self.id = id
        self._origins = origins

    def get_allowed_origins(self):
        return self._origins


class _Req:
    def __init__(self, headers=None, params=None, base_url="http://localhost:8000/"):
        self.headers = headers or {}
        self.query_params = params or {}
        self.base_url = base_url


def _lookups(row):
    """Patch both lookups so only the matching secret resolves."""
    return (
        patch.object(wr, "_lookup_widget_key",
                     side_effect=lambda k: row if k == row.api_key else None),
        patch.object(wr, "_lookup_widget_admin_key",
                     side_effect=lambda k: row if k == row.admin_key else None),
    )


class TestAdminKeyRequired(unittest.TestCase):

    def setUp(self):
        self.row = _Key()
        self.p1, self.p2 = _lookups(self.row)
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def test_public_key_rejected_on_admin_routes(self):
        req = _Req(headers={"X-Widget-Key": "wk_public"})
        with patch.object(wr, "LEGACY_ADMIN_KEY", False):
            with self.assertRaises(HTTPException) as ctx:
                wr.verify_widget_admin_key(req)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_admin_key_accepted_on_admin_routes(self):
        req = _Req(headers={"X-Widget-Key": "wak_secret"})
        self.assertIs(wr.verify_widget_admin_key(req), self.row)

    def test_public_key_still_works_for_chat(self):
        req = _Req(headers={"X-Widget-Key": "wk_public"})
        self.assertIs(wr.verify_widget_key(req), self.row)

    def test_admin_key_does_not_open_the_public_route(self):
        req = _Req(headers={"X-Widget-Key": "wak_secret"})
        with self.assertRaises(HTTPException):
            wr.verify_widget_key(req)

    def test_legacy_flag_reopens_public_key(self):
        req = _Req(headers={"X-Widget-Key": "wk_public"})
        with patch.object(wr, "LEGACY_ADMIN_KEY", True):
            self.assertIs(wr.verify_widget_admin_key(req), self.row)

    def test_missing_key_is_401(self):
        with self.assertRaises(HTTPException) as ctx:
            wr.verify_widget_admin_key(_Req())
        self.assertEqual(ctx.exception.status_code, 401)


class TestOriginMatching(unittest.TestCase):

    def test_exact_host_matches(self):
        self.assertTrue(wr._origin_matches("https://victim.com", "https://victim.com"))
        self.assertTrue(wr._origin_matches("https://victim.com/page", "https://victim.com"))

    def test_suffix_lookalike_rejected(self):
        # The bug: startswith() let this through.
        self.assertFalse(wr._origin_matches("https://victim.com.evil.com", "https://victim.com"))

    def test_other_host_rejected(self):
        self.assertFalse(wr._origin_matches("https://evil.com", "https://victim.com"))

    def test_scheme_must_match(self):
        self.assertFalse(wr._origin_matches("http://victim.com", "https://victim.com"))

    def test_port_must_match(self):
        self.assertFalse(wr._origin_matches("https://victim.com:8443", "https://victim.com"))

    def test_subdomain_needs_wildcard(self):
        self.assertFalse(wr._origin_matches("https://app.victim.com", "https://victim.com"))
        self.assertTrue(wr._origin_matches("https://app.victim.com", "*.victim.com"))

    def test_garbage_origin_rejected(self):
        self.assertFalse(wr._origin_matches("not a url", "https://victim.com"))


class TestCheckOrigin(unittest.TestCase):

    def test_allowlisted_origin_passes(self):
        row = _Key(origins=["https://victim.com"])
        wr._check_origin(_Req(headers={"origin": "https://victim.com"}), row)

    def test_mismatch_rejected_in_strict_mode(self):
        row = _Key(origins=["https://victim.com"])
        with patch.object(wr, "ORIGIN_STRICT", True):
            with self.assertRaises(HTTPException) as ctx:
                wr._check_origin(_Req(headers={"origin": "https://victim.com.evil.com"}), row)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mismatch_only_logged_when_not_strict(self):
        row = _Key(origins=["https://victim.com"])
        with patch.object(wr, "ORIGIN_STRICT", False):
            wr._check_origin(_Req(headers={"origin": "https://evil.com"}), row)

    def test_server_own_origin_always_allowed(self):
        row = _Key(origins=["https://victim.com"])
        with patch.object(wr, "ORIGIN_STRICT", True):
            wr._check_origin(
                _Req(headers={"origin": "http://localhost:8000"}, base_url="http://localhost:8000/"),
                row,
            )

    def test_missing_origin_allowed_for_chat(self):
        # Server-side integrations call the chat API without a browser origin.
        row = _Key(origins=["https://victim.com"])
        with patch.object(wr, "ORIGIN_STRICT", True):
            wr._check_origin(_Req(), row)

    def test_missing_origin_can_be_required(self):
        row = _Key(origins=["https://victim.com"])
        with self.assertRaises(HTTPException):
            wr._check_origin(_Req(), row, require_origin=True)


class TestAdminRoutesWiring(unittest.TestCase):
    """Every /widget/api admin route must depend on the admin key."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "server", "widget_routes.py"), encoding="utf-8") as fh:
            self.source = fh.read()

    def test_no_admin_route_uses_the_public_dependency(self):
        blocks = re.findall(
            r'@admin_api_router\.(get|post|put|delete)\("([^"]+)"\)\s*\n(.*?)(?=\n@|\nclass |\Z)',
            self.source, re.S,
        )
        self.assertGreater(len(blocks), 5, "admin routes not found — parser out of date")
        for method, path, body in blocks:
            self.assertIn(
                "verify_widget_admin_key", body,
                f"{method.upper()} {path} still accepts the public widget key",
            )

    def test_agent_response_hides_credentials(self):
        view = self.source[self.source.index("def _widget_agent_view"):]
        view = view[:view.index("\n\n\n")]
        returned = view[view.index("return {"):]  # skip the docstring, check the payload
        for leaked in ("mcp_servers_config", "tool_config", "guardrail_config"):
            self.assertNotIn(leaked, returned, f"{leaked} exposed to widget admin callers")


if __name__ == "__main__":
    unittest.main()
