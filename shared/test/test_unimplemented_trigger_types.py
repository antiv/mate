#!/usr/bin/env python3
"""
Tests that unfireable trigger types cannot be created (#76).

`file_watch` and `event_bus` are accepted by the model and offered by the
dashboard, but `_execute_trigger_sync` skips them with "not yet implemented".
A user could therefore save a trigger that looks like automation and never runs.

Creating one is now refused. Rows that already exist keep loading, listing and
being editable — in particular they can still be disabled, which is the one
thing an operator stuck with a dead trigger actually wants to do.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import AgentTrigger, Base, Project
from shared.utils.trigger_runner import UNIMPLEMENTED_TRIGGER_TYPES, TriggerRunner


class TestUnimplementedTriggerTypes(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.db_client = MagicMock()
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.patcher = patch("shared.utils.database_client.get_database_client",
                             return_value=self.db_client)
        self.patcher.start()

        session = self.Session()
        session.add(Project(id=1, name="P"))
        session.commit()
        session.close()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.utils.dashboard.dashboard_server import DashboardServer

        project_root = Path(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        app = FastAPI()
        self.server = DashboardServer(app, project_root)
        self.server.db_client = self.db_client
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "admin"
        self.client = TestClient(app)

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _body(self, trigger_type):
        return {"name": "t", "trigger_type": trigger_type, "agent_name": "a1",
                "project_id": 1, "prompt": "go", "cron_expression": "0 * * * *"}

    def _seed_legacy(self, trigger_type="file_watch"):
        session = self.Session()
        row = AgentTrigger(name="legacy", trigger_type=trigger_type, agent_name="a1",
                           project_id=1, prompt="go", is_enabled=True)
        session.add(row)
        session.commit()
        row_id = row.id
        session.close()
        return row_id

    def test_creating_an_unfireable_type_is_refused(self):
        for trigger_type in UNIMPLEMENTED_TRIGGER_TYPES:
            with self.subTest(trigger_type=trigger_type):
                resp = self.client.post("/dashboard/api/triggers", json=self._body(trigger_type))
                self.assertEqual(resp.status_code, 400)
                self.assertIn("never fire", resp.json()["detail"])

    def test_nothing_is_stored_when_refused(self):
        self.client.post("/dashboard/api/triggers", json=self._body("file_watch"))
        session = self.Session()
        self.assertEqual(session.query(AgentTrigger).count(), 0)
        session.close()

    def test_working_types_are_still_accepted(self):
        for trigger_type in ("cron", "webhook"):
            with self.subTest(trigger_type=trigger_type):
                resp = self.client.post("/dashboard/api/triggers", json=self._body(trigger_type))
                self.assertEqual(resp.status_code, 200)

    def test_converting_a_working_trigger_into_one_is_refused(self):
        resp = self.client.post("/dashboard/api/triggers", json=self._body("cron"))
        trigger_id = resp.json()["trigger"]["id"]
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"trigger_type": "event_bus"})
        self.assertEqual(resp.status_code, 400)

        session = self.Session()
        self.assertEqual(session.get(AgentTrigger, trigger_id).trigger_type, "cron")
        session.close()

    def test_a_legacy_row_can_still_be_disabled(self):
        # The one thing an operator stuck with a dead trigger needs to do.
        trigger_id = self._seed_legacy()
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"trigger_type": "file_watch", "is_enabled": False})
        self.assertEqual(resp.status_code, 200)

        session = self.Session()
        row = session.get(AgentTrigger, trigger_id)
        self.assertFalse(row.is_enabled)
        self.assertEqual(row.trigger_type, "file_watch")
        session.close()

    def test_a_legacy_row_still_lists(self):
        self._seed_legacy()
        resp = self.client.get("/dashboard/api/triggers")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([t["trigger_type"] for t in resp.json()["triggers"]], ["file_watch"])

    def test_firing_a_legacy_row_still_reports_skipped(self):
        runner = TriggerRunner()
        runner._invoke_agent = MagicMock()
        trigger = MagicMock(id=1, trigger_type="file_watch")
        result = runner._execute_trigger_sync(trigger)
        self.assertEqual(result["status"], "skipped")
        runner._invoke_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
