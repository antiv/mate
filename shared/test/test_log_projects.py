#!/usr/bin/env python3
"""
project_id on log rows (V044, #276 item 3): written with the row, and filled in
for older rows by the backfill.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import log_projects
from shared.utils.models import (AgentConfig, AgentResponse, AuditLog, Base, GuardrailLog, Project,
                                 TokenUsageLog)


class _Db(unittest.TestCase):

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        db = MagicMock()
        db.get_session.side_effect = lambda: self.Session()
        db.is_connected.return_value = True
        for target in ("shared.utils.log_projects.get_database_client",
                       "shared.utils.token_usage_service.get_database_client",
                       "shared.utils.guardrail_log_service.get_database_client",
                       "shared.utils.response_metrics.get_database_client",
                       "shared.utils.audit_service.get_database_client"):
            p = patch(target, return_value=db)
            p.start()
            self.addCleanup(p.stop)
        session = self.Session()
        session.add_all([Project(id=1, name="Shop"), Project(id=2, name="Support"),
                         AgentConfig(name="seller", type="llm", project_id=1),
                         AgentConfig(name="helper", type="llm", project_id=2)])
        session.commit()
        session.close()

    def _all(self, model):
        session = self.Session()
        try:
            return session.query(model).order_by(model.id).all()
        finally:
            session.close()


class TestWrittenWithTheRow(_Db):

    def test_token_usage(self):
        from shared.utils.token_usage_service import TokenUsageService
        with patch("shared.utils.model_pricing.cost_usd", return_value=None):
            TokenUsageService().insert_token_usage({"agent_name": "helper", "model_name": "m"})
        self.assertEqual(self._all(TokenUsageLog)[0].project_id, 2)

    def test_guardrail_hit(self):
        from shared.utils.guardrail_log_service import GuardrailLogService
        GuardrailLogService().log_trigger("r", "pii", "input", "block", agent_name="seller")
        self.assertEqual(self._all(GuardrailLog)[0].project_id, 1)

    def test_response(self):
        from shared.utils.response_metrics import start_response
        with patch("shared.utils.response_metrics.resolve_origin", return_value="chat"):
            start_response("inv", agent_name="seller")
        self.assertEqual(self._all(AgentResponse)[0].project_id, 1)

    def test_an_unknown_agent_has_no_project(self):
        from shared.utils.guardrail_log_service import GuardrailLogService
        GuardrailLogService().log_trigger("r", "pii", "input", "block", agent_name="gone")
        self.assertIsNone(self._all(GuardrailLog)[0].project_id)

    def test_audit_entries(self):
        from shared.utils import audit_service
        audit_service.log("admin", "agent_update", "agent", resource_id="helper")
        audit_service.log("admin", "project_delete", "project", resource_id="7")
        audit_service.log("admin", "user_create", "user", resource_id="bob")
        audit_service.log("admin", "agent_delete", "agent", resource_id="already_gone", project_id=1)
        self.assertEqual([e.project_id for e in self._all(AuditLog)], [2, 7, None, 1])


class TestBackfill(_Db):

    def _old_rows(self):
        session = self.Session()
        for agent in ("seller", "helper", "gone", None, "seller"):
            session.add(TokenUsageLog(request_id="r", agent_name=agent))
        session.add(GuardrailLog(request_id="r", guardrail_type="pii", action_taken="block", agent_name="helper"))
        session.add(AgentResponse(invocation_id="i", agent_name="seller", started_at=__import__("datetime").datetime.now()))
        for resource_type, resource_id in (("agent", "seller"), ("project", "2"), ("project", "99"),
                                           ("user", "bob"), ("project", "not-a-number")):
            session.add(AuditLog(actor="admin", action="x", resource_type=resource_type, resource_id=resource_id))
        session.commit()
        session.close()

    def test_fills_rows_from_the_agents_project_in_batches(self):
        self._old_rows()
        updated = log_projects.backfill(batch_size=2)
        self.assertEqual([r.project_id for r in self._all(TokenUsageLog)], [1, 2, None, None, 1])
        self.assertEqual(self._all(GuardrailLog)[0].project_id, 2)
        self.assertEqual(self._all(AgentResponse)[0].project_id, 1)
        # A project that no longer exists, and names that are not projects, stay empty
        self.assertEqual([e.project_id for e in self._all(AuditLog)], [1, 2, None, None, None])
        self.assertEqual(updated["token_usage_logs"], 3)

    def test_leaves_rows_that_have_a_project(self):
        session = self.Session()
        session.add(TokenUsageLog(request_id="r", agent_name="seller", project_id=2))  # written before a move
        session.commit()
        session.close()
        log_projects.backfill()
        self.assertEqual(self._all(TokenUsageLog)[0].project_id, 2)

    def test_runs_once(self):
        self._old_rows()
        stored = {}
        with patch("shared.utils.system_settings.get_setting", side_effect=stored.get), \
                patch("shared.utils.system_settings.set_setting",
                      side_effect=lambda key, value, actor: stored.update({key: value})), \
                patch.object(log_projects, "backfill", wraps=log_projects.backfill) as backfill:
            log_projects.run_backfill_once()
            log_projects.run_backfill_once()
        backfill.assert_called_once()
        self.assertEqual(stored, {log_projects.BACKFILL_DONE_KEY: "done",
                                  log_projects.WORKSPACE_BACKFILL_DONE_KEY: "done"})

    def test_a_failed_run_is_retried_next_start(self):
        stored = {}
        with patch("shared.utils.system_settings.get_setting", side_effect=stored.get), \
                patch("shared.utils.system_settings.set_setting",
                      side_effect=lambda key, value, actor: stored.update({key: value})), \
                patch.object(log_projects, "backfill", side_effect=RuntimeError("database gone")):
            log_projects.run_backfill_once()
        self.assertEqual(stored, {})


if __name__ == "__main__":
    unittest.main()
