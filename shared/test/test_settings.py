#!/usr/bin/env python3
"""
Environment defaults read in more than one place must agree (#157).

The migration system used to fall back to PostgreSQL and `mate_agent.db` while
the application used SQLite and `my_agent_data.db`, and the session store used
port 5432 for MySQL. These tests pin each consumer to shared/utils/settings.py.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import settings

SERVER_ENV = {"DB_USER": "mate", "DB_PASSWORD": "secret", "DB_HOST": "db"}


class TestDefaults(unittest.TestCase):

    @patch.dict(os.environ, {}, clear=True)
    def test_unset_means_sqlite_in_the_project_root(self):
        self.assertEqual(settings.db_type(), "sqlite")
        self.assertEqual(settings.db_path(), str(settings.PROJECT_ROOT / "my_agent_data.db"))
        self.assertEqual(settings.database_url(), f"sqlite:///{settings.PROJECT_ROOT / 'my_agent_data.db'}")

    @patch.dict(os.environ, {"DB_PATH": "/data/mate.db"}, clear=True)
    def test_an_absolute_path_is_kept(self):
        self.assertEqual(settings.db_path(), "/data/mate.db")

    @patch.dict(os.environ, {}, clear=True)
    def test_the_port_follows_the_database_type(self):
        self.assertEqual(settings.db_port("postgresql"), "5432")
        self.assertEqual(settings.db_port("mysql"), "3306")
        with patch.dict(os.environ, {"DB_PORT": "6000"}):
            self.assertEqual(settings.db_port("mysql"), "6000")

    @patch.dict(os.environ, {"DB_TYPE": "MySQL", **SERVER_ENV}, clear=True)
    def test_mysql_url(self):
        self.assertEqual(settings.database_url(), "mysql+pymysql://mate:secret@db:3306/mate_agent")

    @patch.dict(os.environ, {"DB_TYPE": "postgresql", "DB_USER": "mate"}, clear=True)
    def test_a_server_database_needs_user_and_password(self):
        with self.assertRaisesRegex(ValueError, "DB_USER and DB_PASSWORD"):
            settings.database_url()

    @patch.dict(os.environ, {"DB_TYPE": "oracle"}, clear=True)
    def test_an_unknown_type_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            settings.database_url()

    @patch.dict(os.environ, {"DB_TYPE": "postgresql", **SERVER_ENV}, clear=True)
    def test_database_info_has_no_credentials(self):
        info = settings.database_info()
        self.assertEqual(info, {"type": "POSTGRESQL", "hostname": "db", "filename": None,
                                "database": "mate_agent", "port": "5432"})

    @patch.dict(os.environ, {}, clear=True)
    def test_one_supabase_bucket_and_artifact_service(self):
        self.assertEqual(settings.supabase_bucket(), "artifacts")
        self.assertEqual(settings.artifact_service(), "none")


class TestConsumersAgree(unittest.TestCase):

    def _client_url(self):
        from shared.utils.database_client import DatabaseClient
        with patch.object(DatabaseClient, "_initialize_connection"):
            return DatabaseClient()._get_database_url()

    def _migration_url(self):
        from shared.utils.migration_system import MigrationSystem
        with patch("sqlalchemy.create_engine") as create_engine:
            MigrationSystem(database_client=None)._get_engine()
        return create_engine.call_args[0][0]

    @patch.dict(os.environ, {}, clear=True)
    def test_migrations_without_a_client_use_the_applications_database(self):
        # Before #157 this fallback targeted PostgreSQL, or mate_agent.db for SQLite
        self.assertEqual(self._migration_url(), self._client_url())

    @patch.dict(os.environ, {"DB_TYPE": "mysql", **SERVER_ENV}, clear=True)
    def test_mysql_sessions_use_the_mysql_port(self):
        from shared.utils.utils import build_session_service_uri
        self.assertIn("@db:3306/", build_session_service_uri())
        self.assertIn("@db:3306/", self._client_url())

    @patch.dict(os.environ, {}, clear=True)
    def test_sqlite_sessions_share_the_database_file(self):
        from shared.utils.utils import build_session_service_uri
        self.assertEqual(build_session_service_uri(), self._client_url())

    @patch.dict(os.environ, {"DB_TYPE": "postgresql", **SERVER_ENV}, clear=True)
    def test_dashboard_and_client_report_the_same_database(self):
        from shared.utils.database_client import DatabaseClient
        with patch.object(DatabaseClient, "_initialize_connection"):
            client = DatabaseClient()
        self.assertEqual(client.get_connection_info(), settings.database_info())


if __name__ == "__main__":
    unittest.main()
