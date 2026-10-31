#!/usr/bin/env python3
"""
The suite runs on its own database and without .env (shared/test/__init__.py).

Run without -t, unittest loads the test modules outside the shared.test package,
so that setup never runs: the tests then migrate and write into the database .env
names. By the time this test runs the damage is done, but it says so.
"""

import os
import tempfile
import unittest

COMMAND = 'python -m unittest discover -s shared/test -t . -p "test_*.py"'


class TestIsolatedEnvironment(unittest.TestCase):

    def test_the_database_is_a_temporary_one(self):
        self.assertEqual(os.environ.get("DB_TYPE"), "sqlite", f"Run the tests with: {COMMAND}")
        self.assertTrue(
            os.path.realpath(os.environ.get("DB_PATH", "")).startswith(
                os.path.realpath(tempfile.gettempdir())),
            f"The tests used the configured database. Run them with: {COMMAND}")

    def test_dotenv_is_not_loaded(self):
        self.assertEqual(os.environ.get("PYTHON_DOTENV_DISABLED"), "1",
                         f"The tests loaded .env. Run them with: {COMMAND}")


if __name__ == "__main__":
    unittest.main()
