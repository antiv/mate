"""
Test package for MATE.

The tests run on a database of their own, never on the one .env configures.
Importing shared.utils builds the agent manager, which connects to the configured
database and applies every pending migration to it; and tests that do not patch
the database client write audit entries, responses and users into it. So, before
any test module is imported:

- .env is not loaded (PYTHON_DOTENV_DISABLED), as in CI, where there is none: a
  developer's settings (rate limits, MATE_ENV, provider keys) do not change what
  the tests see;
- the database is a SQLite file in a temporary directory, removed at exit.

This module only runs when the tests are loaded as the shared.test package:
python -m unittest discover -s shared/test -t . -p "test_*.py"
"""

import atexit
import os
import shutil
import tempfile

_tmp = tempfile.mkdtemp(prefix="mate-test-")
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DB_TYPE"] = "sqlite"
os.environ["DB_PATH"] = os.path.join(_tmp, "mate.db")
