#!/usr/bin/env python3
"""
The sync PostgreSQL URL must name psycopg2 explicitly: from SQLAlchemy 2.1 a bare
postgresql:// selects psycopg (v3), which is not installed.
"""

import os
import sys
import unittest
from unittest.mock import patch

from sqlalchemy.engine import make_url

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PG_ENV = {
    "DB_TYPE": "postgresql",
    "DB_HOST": "db",
    "DB_PORT": "5432",
    "DB_NAME": "mate",
    "DB_USER": "mate",
    "DB_PASSWORD": "secret",
}


class TestPostgresDriverUrl(unittest.TestCase):

    @patch.dict(os.environ, PG_ENV, clear=True)
    def test_database_client_url_uses_psycopg2(self):
        from shared.utils.database_client import DatabaseClient
        with patch.object(DatabaseClient, "_initialize_connection"):
            client = DatabaseClient()
        url = make_url(client._get_database_url())
        self.assertEqual(url.drivername, "postgresql+psycopg2")

    @patch.dict(os.environ, PG_ENV, clear=True)
    def test_migration_system_engine_uses_psycopg2(self):
        from shared.utils import migration_system
        with patch("sqlalchemy.create_engine") as create_engine:
            system = migration_system.MigrationSystem(database_client=None)
            system._get_engine()
        url = make_url(create_engine.call_args[0][0])
        self.assertEqual(url.drivername, "postgresql+psycopg2")


if __name__ == "__main__":
    unittest.main()
