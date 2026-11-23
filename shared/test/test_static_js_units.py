#!/usr/bin/env python3
"""
The dashboard scripts' unit tests, in shared/test/js/, run with node's test runner.

They cover the parts of a script that need no page: the shared table's searching,
sorting and paging (static/js/data-table.js), how the approval settings editor
reads and writes require_confirmation (static/js/approval-settings.js), and how the
Usage tab's charts read the hourly counts (static/js/usage-chart.js).
"""

import shutil
import subprocess
import unittest
from pathlib import Path

JS_TESTS = Path(__file__).parent / "js"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestStaticJsUnits(unittest.TestCase):

    def test_js_unit_tests_pass(self):
        files = sorted(JS_TESTS.glob("*.test.js"))
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=path.name):
                result = subprocess.run(["node", "--test", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
