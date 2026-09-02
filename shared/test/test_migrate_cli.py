#!/usr/bin/env python3
"""
Tests that the documented migration CLI actually runs.

`shared/migrate.py` imported its dependency relatively (`from .utils...`) while
being invoked as a script, so `python shared/migrate.py run` — the form
documented in CLAUDE.md, README.md and CONTRIBUTING.md — died on ImportError
before reaching any command. The `sys.path.append(parent)` already at the top of
the file shows script invocation was the intent all along.

Invoked with no arguments the CLI prints usage and exits before constructing
MigrationSystem, so these tests never touch a database.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestMigrateCLIIsRunnable(unittest.TestCase):

    def _run(self, args):
        return subprocess.run(
            [sys.executable] + args,
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )

    def test_documented_script_invocation_reaches_the_cli(self):
        # The form in CLAUDE.md / README.md / CONTRIBUTING.md.
        result = self._run(["shared/migrate.py"])
        self.assertNotIn("ImportError", result.stderr)
        self.assertNotIn("attempted relative import", result.stderr)
        self.assertIn("Usage: python migrate.py", result.stdout)

    def test_module_invocation_still_works(self):
        # The workaround people reach for; it must not regress.
        result = self._run(["-m", "shared.migrate"])
        self.assertNotIn("ImportError", result.stderr)
        self.assertIn("Usage: python migrate.py", result.stdout)

    def test_the_commands_the_docs_promise_are_all_recognised(self):
        usage = self._run(["shared/migrate.py"]).stdout
        for command in ("run", "status", "create", "rollback"):
            self.assertIn(command, usage)


if __name__ == "__main__":
    unittest.main()
