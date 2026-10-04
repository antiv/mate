#!/usr/bin/env python3
"""
Triggers must not look healthy when nothing can run or be delivered (#151).

A cron expression the scheduler cannot use, an http_callback without a URL and
an email output without a recipient or SMTP_HOST used to be saved, and runs
with them were recorded as `ok`. The API now refuses them at create and
update, and a run that cannot deliver is recorded as an error.
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
from shared.utils.trigger_runner import TriggerRunner, parse_cron_expression


class TestParseCronExpression(unittest.TestCase):

    def test_a_valid_expression_parses(self):
        self.assertIsNotNone(parse_cron_expression("*/15 9-17 * * mon-fri"))

    def test_invalid_expressions_raise_with_a_reason(self):
        cases = {
            None: "empty",
            "  ": "empty",
            "0 * * *": "5 fields",
            "0 0 * * * *": "5 fields",
            "61 * * * *": "invalid",
            "0 0 * * funday": "invalid",
        }
        for expr, reason in cases.items():
            with self.subTest(expr=expr):
                with self.assertRaisesRegex(ValueError, reason):
                    parse_cron_expression(expr)


class TriggerApiTestCase(unittest.TestCase):

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
        self.patchers = [
            patch("shared.utils.database_client.get_database_client",
                  return_value=self.db_client),
            patch.dict(os.environ, {"SMTP_HOST": ""}),
        ]
        for p in self.patchers:
            p.start()

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
        for p in reversed(self.patchers):
            p.stop()
        self.engine.dispose()

    def _body(self, **overrides):
        body = {"name": "t", "trigger_type": "cron", "agent_name": "a1",
                "project_id": 1, "prompt": "go", "cron_expression": "0 * * * *"}
        body.update(overrides)
        return body

    def _create(self, **overrides):
        return self.client.post("/dashboard/api/triggers", json=self._body(**overrides))

    def _count(self):
        session = self.Session()
        try:
            return session.query(AgentTrigger).count()
        finally:
            session.close()

    def _seed(self, **fields):
        # Rows saved before validation existed.
        session = self.Session()
        row = AgentTrigger(name="old", agent_name="a1", project_id=1, prompt="go",
                           is_enabled=True, **fields)
        session.add(row)
        session.commit()
        row_id = row.id
        session.close()
        return row_id


class TestCronValidation(TriggerApiTestCase):

    def test_an_invalid_cron_expression_is_refused_at_create(self):
        for expr in ("", "0 * * *", "61 * * * *"):
            with self.subTest(expr=expr):
                resp = self._create(cron_expression=expr)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("cron_expression", resp.json()["detail"])
        self.assertEqual(self._count(), 0)

    def test_a_valid_cron_expression_is_accepted(self):
        self.assertEqual(self._create().status_code, 200)

    def test_a_webhook_trigger_needs_no_cron_expression(self):
        self.assertEqual(self._create(trigger_type="webhook", cron_expression=None).status_code, 200)

    def test_an_invalid_cron_expression_is_refused_at_update(self):
        trigger_id = self._create().json()["trigger"]["id"]
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"cron_expression": "every hour"})
        self.assertEqual(resp.status_code, 400)
        session = self.Session()
        self.assertEqual(session.get(AgentTrigger, trigger_id).cron_expression, "0 * * * *")
        session.close()

    def test_switching_a_webhook_to_cron_without_an_expression_is_refused(self):
        trigger_id = self._create(trigger_type="webhook", cron_expression=None).json()["trigger"]["id"]
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"trigger_type": "cron"})
        self.assertEqual(resp.status_code, 400)

    def test_an_old_row_with_a_bad_expression_can_still_be_disabled(self):
        trigger_id = self._seed(trigger_type="cron", cron_expression="bogus")
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"is_enabled": False})
        self.assertEqual(resp.status_code, 200)


class TestOutputValidation(TriggerApiTestCase):

    def test_http_callback_without_a_url_is_refused(self):
        resp = self._create(output_type="http_callback", output_config={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("url", resp.json()["detail"])
        self.assertEqual(self._count(), 0)

    def test_email_without_smtp_host_is_refused(self):
        resp = self._create(output_type="email", output_config={"to": "a@b.c"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("SMTP_HOST", resp.json()["detail"])

    def test_email_without_a_recipient_is_refused(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            resp = self._create(output_type="email", output_config={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("'to'", resp.json()["detail"])

    def test_a_complete_email_output_is_accepted(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            resp = self._create(output_type="email", output_config={"to": "a@b.c"})
        self.assertEqual(resp.status_code, 200)

    def test_breaking_the_output_at_update_is_refused(self):
        trigger_id = self._create(output_type="http_callback",
                                  output_config={"url": "https://x"}).json()["trigger"]["id"]
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"output_config": {}})
        self.assertEqual(resp.status_code, 400)

    def test_rotating_a_key_does_not_revalidate_an_untouched_output(self):
        # An older webhook trigger with an email output on a server without
        # SMTP_HOST must still be able to rotate its key.
        trigger_id = self._seed(trigger_type="webhook", output_type="email",
                                output_config='{"to": "a@b.c"}')
        resp = self.client.put(f"/dashboard/api/triggers/{trigger_id}",
                               json={"regenerate_fire_key": True})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("fire_key", resp.json())


class TestUndeliverableRunIsAnError(unittest.TestCase):

    def test_a_run_that_cannot_deliver_is_recorded_as_error(self):
        runner = TriggerRunner()
        runner._invoke_agent = MagicMock(return_value="the answer")
        trigger = MagicMock(id=1, trigger_type="cron", prompt="go", output_type="http_callback")
        trigger.get_output_config.return_value = {}
        result = runner._execute_trigger_sync(trigger)
        self.assertEqual(result["status"], "error")
        self.assertIn("url", result["message"])
        # The agent did run; its answer is kept so Last Run can show it.
        self.assertEqual(result["agent_response"], "the answer")


if __name__ == "__main__":
    unittest.main()
