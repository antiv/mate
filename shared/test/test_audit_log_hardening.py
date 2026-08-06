#!/usr/bin/env python3
"""
Tests for the audit-log hardening.

The audit page concatenated actor/action/resource/ip/details straight into
innerHTML, and ip_address came from a raw X-Forwarded-For header — so an
unauthenticated request could store script that ran in an admin's browser.
"""

import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.audit_service import _client_ip

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUDIT_TEMPLATE = os.path.join(REPO_ROOT, "templates", "dashboard", "audit_logs.html")


class _Client:
    def __init__(self, host):
        self.host = host


class _Req:
    def __init__(self, headers=None, host="10.0.0.9"):
        self.headers = headers or {}
        self.client = _Client(host)


class TestClientIp(unittest.TestCase):

    def test_forwarded_header_ignored_by_default(self):
        req = _Req({"X-Forwarded-For": "<img src=x onerror=alert(1)>"})
        with patch.dict(os.environ, {"TRUSTED_PROXY_HOSTS": "*"}):
            self.assertEqual(_client_ip(req), "10.0.0.9")

    def test_forwarded_header_ignored_when_unset(self):
        req = _Req({"X-Forwarded-For": "1.2.3.4"})
        env = {k: v for k, v in os.environ.items() if k != "TRUSTED_PROXY_HOSTS"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_client_ip(req), "10.0.0.9")

    def test_forwarded_header_used_when_proxy_configured(self):
        req = _Req({"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})
        with patch.dict(os.environ, {"TRUSTED_PROXY_HOSTS": "10.0.0.1"}):
            self.assertEqual(_client_ip(req), "1.2.3.4")

    def test_no_request_is_none(self):
        self.assertIsNone(_client_ip(None))


class TestAuditTemplateEscaping(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(AUDIT_TEMPLATE, encoding="utf-8") as fh:
            cls.source = fh.read()

    def test_esc_helper_defined(self):
        self.assertIn("function esc(s)", self.source)
        self.assertIn("&#39;", self.source, "esc() must also escape single quotes")

    def test_row_fields_are_escaped(self):
        row = self.source[self.source.index("tbody.innerHTML = logs.map"):]
        row = row[:row.index(").join('');")]
        for field in ("row.actor", "row.action", "row.resource_type", "row.ip_address"):
            self.assertIn(f"esc({field}", row, f"{field} rendered unescaped")
        self.assertNotIn("+ (row.actor", row)
        self.assertNotIn("+ (row.ip_address", row)


if __name__ == "__main__":
    unittest.main()
