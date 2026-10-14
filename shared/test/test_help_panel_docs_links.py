#!/usr/bin/env python3
"""
Help panel must not open made-up https://dashboard/... docs links (#234).

The mate_help agent often rewrites tool-provided relative /dashboard/docs?... links
into https://dashboard/docs?... . safeHref used to accept those as external URLs.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HELP_PANEL = ROOT / "static" / "js" / "help-panel.js"

CASES = [
    # fake hosts → relative docs path
    ("https://dashboard/docs?page=user/agents.md", "/dashboard/docs?page=user/agents.md"),
    ("http://dashboard/docs?page=user/agents.md", "/dashboard/docs?page=user/agents.md"),
    ("//dashboard/docs?page=user/agents.md", "/dashboard/docs?page=user/agents.md"),
    ("dashboard/docs?page=user/agents.md", "/dashboard/docs?page=user/agents.md"),
    ("https://dashboard/docs#top", "/dashboard/docs#top"),
    ("https://dashboard/docs", "/dashboard/docs"),
    # already relative / real external — unchanged
    ("/dashboard/docs?page=user/agents.md", "/dashboard/docs?page=user/agents.md"),
    ("https://example.com/guide", "https://example.com/guide"),
    ("https://dashboard.example.com/docs?page=x", "https://dashboard.example.com/docs?page=x"),
    ("https://evil.com/dashboard/docs?page=x", "https://evil.com/dashboard/docs?page=x"),
]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestHelpPanelDocsLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = HELP_PANEL.read_text(encoding="utf-8")
        match = re.search(
            r"function normalizeDocsHref\(url\) \{.*?\n  \}",
            src,
            re.DOTALL,
        )
        if not match:
            raise AssertionError("normalizeDocsHref not found in help-panel.js")
        cls.normalize_fn = match.group(0)
        # Also pull safeHref + a tiny esc for link rendering checks
        safe = re.search(r"function safeHref\(url\) \{.*?\n  \}", src, re.DOTALL)
        cls.safe_fn = safe.group(0) if safe else ""

    def _node(self, script: str) -> str:
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_normalize_docs_href_cases(self):
        script = (
            self.normalize_fn
            + "\n"
            + "const cases = "
            + json.dumps(CASES)
            + ";\n"
            + "for (const [input, expected] of cases) {\n"
            + "  const got = normalizeDocsHref(input);\n"
            + "  if (got !== expected) {\n"
            + "    console.error(JSON.stringify({input, expected, got}));\n"
            + "    process.exit(1);\n"
            + "  }\n"
            + "}\n"
            + "console.log('ok');\n"
        )
        self.assertEqual(self._node(script), "ok")

    def test_fake_host_markdown_link_is_internal(self):
        # Relative docs links must not get target=_blank after normalization.
        script = (
            "function esc(s) {\n"
            "  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
            ".replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');\n"
            "}\n"
            + self.safe_fn
            + "\n"
            + self.normalize_fn
            + "\n"
            + "function renderLink(href) {\n"
            + "  var safe = safeHref(normalizeDocsHref(href.replace(/&amp;/g, '&')));\n"
            + "  if (!safe) return '';\n"
            + "  var external = /^https?:/i.test(safe);\n"
            + "  return '<a href=\"' + esc(safe) + '\"'"
            + " + (external ? ' target=\"_blank\" rel=\"noopener\"' : '') + '>x</a>';\n"
            + "}\n"
            + "const out = renderLink('https://dashboard/docs?page=user/agents.md');\n"
            + "if (out !== '<a href=\"/dashboard/docs?page=user/agents.md\">x</a>') {\n"
            + "  console.error(out); process.exit(1);\n"
            + "}\n"
            + "const ext = renderLink('https://example.com/a');\n"
            + "if (!ext.includes('target=\"_blank\"') || !ext.includes('https://example.com/a')) {\n"
            + "  console.error(ext); process.exit(1);\n"
            + "}\n"
            + "console.log('ok');\n"
        )
        self.assertEqual(self._node(script), "ok")

    def test_help_panel_wires_normalize_before_safe_href(self):
        src = HELP_PANEL.read_text(encoding="utf-8")
        self.assertIn("normalizeDocsHref(href.replace(/&amp;/g, \"&\"))", src)
        self.assertIn("function normalizeDocsHref(url)", src)


if __name__ == "__main__":
    unittest.main()
