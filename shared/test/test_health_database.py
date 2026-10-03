"""
/health reports the database, and DatabaseClient retries a database that is
still starting before giving up.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# auth_server sets OTEL_SDK_DISABLED on import; keep that out of other tests.
with patch.dict(os.environ):
    import auth_server
from shared.utils.database_client import DatabaseClient


def _client(connected: bool) -> MagicMock:
    client = MagicMock()
    client.is_connected.return_value = connected
    return client


class TestHealthCheckDatabase(unittest.TestCase):

    def test_healthy_when_database_is_reachable(self):
        with patch.object(auth_server, "get_database_client", return_value=_client(True)):
            body = asyncio.run(auth_server.health_check())
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(body["database"], "available")

    def test_503_naming_the_database_when_unreachable(self):
        with patch.object(auth_server, "get_database_client", return_value=_client(False)):
            response = asyncio.run(auth_server.health_check())
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.body)
        self.assertEqual(body["status"], "unhealthy")
        self.assertEqual(body["database"], "unavailable")


class TestDatabaseConnectRetry(unittest.TestCase):

    def _engine(self, failures: int) -> MagicMock:
        engine = MagicMock()
        engine.connect.side_effect = [ConnectionError("refused")] * failures + [MagicMock()]
        return engine

    def _init(self, engine: MagicMock, retries: str) -> DatabaseClient:
        env = {"DB_TYPE": "postgresql", "DB_NAME": "mate", "DB_USER": "u",
               "DB_PASSWORD": "p", "DB_CONNECT_RETRIES": retries}
        with patch.dict(os.environ, env), \
                patch("shared.utils.database_client.create_engine", return_value=engine), \
                patch("shared.utils.database_client.time.sleep") as sleep, \
                patch.object(DatabaseClient, "_run_migrations"), \
                patch.object(DatabaseClient, "_auto_create_tables"), \
                patch.object(DatabaseClient, "_run_audit_retention"):
            client = DatabaseClient()
        self.sleeps = sleep.call_count
        return client

    def test_connects_once_the_database_comes_up(self):
        client = self._init(self._engine(failures=2), retries="5")
        self.assertIsNotNone(client._engine)
        self.assertEqual(self.sleeps, 2)

    def test_gives_up_after_the_configured_attempts(self):
        engine = self._engine(failures=3)
        client = self._init(engine, retries="3")
        self.assertIsNone(client._engine)
        self.assertEqual(engine.connect.call_count, 3)


if __name__ == "__main__":
    unittest.main()
