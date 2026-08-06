#!/usr/bin/env python3
"""
Regression tests for the /builder/* path traversal fix.

The builder endpoints in adk_main.py built filesystem paths straight from
user input (``file_path`` query param, ``app_name`` path param, upload
filenames), allowing reads and writes outside the agents directory.
All three now route through resolve_within_base().

adk_main.py cannot be imported here (it calls parse_args() and registers
services at import time), so these tests cover the containment helper with
the exact payloads the endpoints pass through, plus a source-level check
that the handlers still use it.
"""

import unittest
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import HTTPException

from shared.utils.path_safety import resolve_within_base

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_BASE = REPO_ROOT / "agents"


class TestResolveWithinBase(unittest.TestCase):
    """Containment for the payloads from the report."""

    def test_normal_file_allowed(self):
        target = resolve_within_base(AGENTS_BASE, "my_agent", "root_agent.yaml")
        self.assertEqual(target, AGENTS_BASE / "my_agent" / "root_agent.yaml")

    def test_nested_file_allowed(self):
        target = resolve_within_base(AGENTS_BASE, "my_agent", "tmp", "my_agent", "sub.yaml")
        self.assertTrue(target.is_relative_to(AGENTS_BASE))

    def test_base_itself_allowed(self):
        self.assertEqual(resolve_within_base(AGENTS_BASE), AGENTS_BASE.resolve())

    def test_dotdot_escape_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            resolve_within_base(AGENTS_BASE, "my_agent", "../../.env")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_absolute_path_rejected(self):
        # Path("/a/b") / "/etc/passwd" silently drops the base.
        with self.assertRaises(HTTPException) as ctx:
            resolve_within_base(AGENTS_BASE, "my_agent", "/etc/passwd")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_absolute_app_name_rejected(self):
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, "/etc", "passwd")

    def test_dotdot_app_name_rejected(self):
        # builder_cancel / get_agent_builder: app_name = ".." reached rmtree.
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, "..")

    def test_dotdot_app_name_with_tmp_rejected(self):
        # builder_build tmp branch: agents/../tmp/.. resolved to the repo root.
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, "..", "tmp", "..")

    def test_upload_filename_escape_rejected(self):
        agent_dir = resolve_within_base(AGENTS_BASE, "my_agent", "tmp", "my_agent")
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, agent_dir, "../../../../auth_server.py")

    def test_agent_folder_delete_cannot_escape(self):
        # DELETE /dashboard/api/agents/{agent_name}/delete-folder reached rmtree.
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, "..")
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, "../..")

    def test_symlink_style_sibling_rejected(self):
        # A sibling directory sharing the base's name prefix must not pass.
        with self.assertRaises(HTTPException):
            resolve_within_base(AGENTS_BASE, "..", "agents_backup")


class TestBuilderHandlersUseHelper(unittest.TestCase):
    """The three handlers must not rebuild raw paths from user input."""

    @classmethod
    def setUpClass(cls):
        cls.source = (REPO_ROOT / "adk_main.py").read_text()
        cls.builder_block = cls.source[cls.source.index('@app.post("/builder/save"'):
                                       cls.source.index("Added builder endpoints")]

    def test_no_raw_joins_in_builder_block(self):
        for bad in ("agent_dir / file_path", "agent_dir / file_name", 'os.path.join(base_path'):
            self.assertNotIn(bad, self.builder_block, f"raw path join reintroduced: {bad}")

    def test_each_handler_resolves(self):
        for handler in ("builder_build", "builder_cancel", "get_agent_builder"):
            start = self.builder_block.index(f"def {handler}(")
            body = self.builder_block[start:]
            next_decorator = body.find("@app.", 1)
            if next_decorator != -1:
                body = body[:next_decorator]
            self.assertIn("_resolve_within_agents", body, f"{handler} bypasses containment")

    def test_upload_filename_split_is_guarded(self):
        # An unguarded split("/") raised ValueError -> 500 on malformed names.
        self.assertNotRegex(self.builder_block, r'\w+,\s*\w+ = file\.filename\.split\("/"\)')
        self.assertRegex(self.builder_block, r"len\(parts\) != 2")


class TestBindDefaults(unittest.TestCase):
    """SECURITY.md: the agent servers must not default to a public bind."""

    def test_agent_servers_default_to_loopback(self):
        for module in ("adk_main.py", "langgraph_main.py"):
            source = (REPO_ROOT / module).read_text()
            match = re.search(r'add_argument\("--host",\s*default="([^"]+)"\)', source)
            self.assertIsNotNone(match, f"--host argument not found in {module}")
            self.assertEqual(match.group(1), "127.0.0.1", f"{module} binds publicly by default")


class TestBuilderProxyGate(unittest.TestCase):
    """/builder/* is admin-only at the auth server proxy."""

    def test_proxy_gates_builder_paths(self):
        source = (REPO_ROOT / "server" / "proxy_routes.py").read_text()
        block = source[source.index("async def proxy_adk(request"):]
        self.assertIn('"builder" in path.split("/")', block)
        self.assertIn("is_admin_user", block)

    def test_gate_matches_both_endpoint_families(self):
        # MATE's own /builder/* and ADK's upstream /dev/apps/{app}/builder*.
        gated = lambda p: "builder" in p.split("/")
        for path in ("builder/save", "builder/app/x", "builder/app/x/cancel",
                     "dev/apps/x/builder", "dev/apps/x/builder/save"):
            self.assertTrue(gated(path), f"{path} not gated")
        for path in ("run_sse", "list-apps", "apps/builderbot/users/u/sessions"):
            self.assertFalse(gated(path), f"{path} gated by mistake")


if __name__ == "__main__":
    unittest.main()
