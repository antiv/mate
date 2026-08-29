#!/usr/bin/env python3
"""
Tests that deleting a template lands in the audit log (#73).

The call site used to invoke `audit_service.log_event`, which does not exist.
Every call raised AttributeError into a bare `except` that logged a warning, so
deletions left no trace at all — the failure mode of an append-only compliance
log looking complete while it is not. These tests pin the row's presence and
shape so a future signature drift fails loudly instead of silently.
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

from shared.utils import audit_service
from shared.utils.models import AuditLog, Base


class TestTemplateDeleteAudit(unittest.TestCase):

    def setUp(self):
        # TestClient serves the request on another thread, so the in-memory
        # database has to be one shared connection rather than one per thread.
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.db_client = MagicMock()
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.audit_patcher = patch("shared.utils.audit_service.get_database_client",
                                   return_value=self.db_client)
        self.audit_patcher.start()

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.utils.dashboard.dashboard_server import DashboardServer

        project_root = Path(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        app = FastAPI()
        self.server = DashboardServer(app, project_root)
        self.server.template_service = MagicMock()
        self.server.template_service.delete_template.return_value = True
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "alice"
        self.client = TestClient(app)

    def tearDown(self):
        self.audit_patcher.stop()
        self.engine.dispose()

    def _rows(self):
        session = self.Session()
        try:
            return session.query(AuditLog).all()
        finally:
            session.close()

    def test_deleting_a_template_writes_an_audit_row(self):
        resp = self.client.delete("/dashboard/api/templates/my-template")
        self.assertEqual(resp.status_code, 200)

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, "template.delete")
        self.assertEqual(rows[0].resource_type, "template")
        self.assertEqual(rows[0].resource_id, "my-template")
        self.assertEqual(rows[0].actor, "alice")

    def test_a_failed_delete_is_not_audited(self):
        # 404 means nothing was deleted; recording it would be a false entry.
        self.server.template_service.delete_template.return_value = False
        resp = self.client.delete("/dashboard/api/templates/missing")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self._rows(), [])

    def test_audit_service_exposes_the_constants_the_call_site_uses(self):
        # The original bug was a call to a name that did not exist, caught by a
        # bare except. Assert the names directly so that cannot recur silently.
        self.assertEqual(audit_service.ACTION_TEMPLATE_DELETE, "template.delete")
        self.assertEqual(audit_service.RESOURCE_TEMPLATE, "template")
        self.assertFalse(hasattr(audit_service, "log_event"))


if __name__ == "__main__":
    unittest.main()
