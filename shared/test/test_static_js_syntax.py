#!/usr/bin/env python3
"""
Every dashboard script must parse.

A script with a syntax error defines none of its functions, and nothing reports it
server side. agent-forms.js lost a line in 1.3.0 and the Agents page's edit, copy
and delete buttons did nothing for three releases.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

STATIC = Path(__file__).parent.parent.parent / "static"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestStaticJsSyntax(unittest.TestCase):

    def test_every_script_parses(self):
        scripts = sorted(STATIC.rglob("*.js"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=str(script.relative_to(STATIC))):
                result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
