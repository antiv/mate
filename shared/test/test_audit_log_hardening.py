#!/usr/bin/env python3
"""
Tests for the audit-log hardening.

The audit page concatenated actor/action/resource/ip/details straight into
innerHTML, and ip_address came from a raw X-Forwarded-For header — so an
unauthenticated request could store script that ran in an admin's browser.
"""

import unittest
from unittest.mock import patch
import shutil
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.audit_service import _client_ip

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUDIT_TEMPLATE = os.path.join(REPO_ROOT, "templates", "dashboard", "audit_logs.html")
INDEX_TEMPLATE = os.path.join(REPO_ROOT, "templates", "dashboard", "index.html")


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



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestRecentActivityEscaping(unittest.TestCase):
    """
    The dashboard home lists the latest audit entries. Their actor can come from a
    chat: a widget visitor picks their own user_id, and a fallback model or an RBAC
    denial records it. Run the list's real code on such an entry.
    """

    def _render(self, entry):
        with open(INDEX_TEMPLATE, encoding="utf-8") as fh:
            source = fh.read()
        start = source.index("    function loadRecentActivity() {")
        if "    function esc(s) {" in source[:start]:
            start = source.index("    function esc(s) {")
        end = source.index("\n    }\n", source.index("Could not load activity.")) + len("\n    }\n")
        script = (
            "const list = {innerHTML: ''};\n"
            "const document = {getElementById: () => list};\n"
            f"const apiCall = () => Promise.resolve({{logs: [{entry}]}});\n"
            + source[start:end]
            + "\nloadRecentActivity();\nsetTimeout(() => process.stdout.write(list.innerHTML), 0);\n"
        )
        return subprocess.run(["node", "-e", script], capture_output=True, text=True,
                              check=True, timeout=30).stdout

    def test_actor_action_and_resource_are_escaped(self):
        html = self._render("""{actor: 'widget_1_<img src=x onerror=alert(1)>',
            action: '<b>agent.model_fallback</b>', resource_type: 'agent',
            resource_id: '<svg onload=alert(2)>', timestamp: new Date().toISOString()}""")
        self.assertIn("widget_1_&lt;img src=x onerror=alert(1)&gt;", html)
        for raw in ("<img", "<svg", "<b>"):
            self.assertNotIn(raw, html)


if __name__ == "__main__":
    unittest.main()
