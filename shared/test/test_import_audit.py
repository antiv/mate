#!/usr/bin/env python3
"""
Tests that the import/sync family lands in the audit log (#82).

Deleting a template wrote an audit row; creating agents in bulk did not. The
result was an asymmetric trail — the log showed agents and templates being
deleted, but not created or bulk-modified. `POST /dashboard/api/agents/import`
with `overwrite=true` can rewrite every agent in an installation, and left no
record of who did it. `ACTION_TEMPLATE_IMPORT` had been defined since the
constant list was written and was never referenced.

These tests pin one row per endpoint, the `overwrite` flag in the details, and
that a failed operation is not audited as if it succeeded.
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


class ImportAuditTestCase(unittest.TestCase):
    """Shared harness: an in-memory audit database behind a live dashboard app."""

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

    def _one_row(self, action, resource_type):
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].action, action)
        self.assertEqual(rows[0].resource_type, resource_type)
        self.assertEqual(rows[0].actor, "alice")
        return rows[0]


class TestAgentsImportAudit(ImportAuditTestCase):

    def setUp(self):
        super().setUp()
        self.server._import_agent_configs = MagicMock(return_value={
            "success": True, "imported_count": 3, "skipped_count": 1, "errors": [],
        })

    def test_importing_agents_writes_an_audit_row(self):
        resp = self.client.post("/dashboard/api/agents/import", json={"agents": []})
        self.assertEqual(resp.status_code, 200)

        row = self._one_row("agents.import", "agent")
        details = row.get_details()
        self.assertEqual(details["imported_count"], 3)
        self.assertEqual(details["skipped_count"], 1)
        self.assertFalse(details["overwrite"])

    def test_overwrite_is_visible_in_the_details(self):
        # overwrite=true is the difference between adding and destroying, so it
        # has to be readable from the row itself.
        resp = self.client.post("/dashboard/api/agents/import?overwrite=true",
                                json={"agents": []})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._one_row("agents.import", "agent").get_details()["overwrite"])

    def test_a_failed_import_is_not_audited(self):
        self.server._import_agent_configs.return_value = {"error": "Invalid import format"}
        resp = self.client.post("/dashboard/api/agents/import", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._rows(), [])


class TestTemplateImportAudit(ImportAuditTestCase):

    def setUp(self):
        super().setUp()
        self.server._import_template = MagicMock(return_value={
            "success": True, "project_id": 7, "project_name": "Support",
            "root_agent_name": "support_root", "agents_created": 5,
            "memory_blocks_created": 2,
        })

    def test_importing_a_template_writes_an_audit_row(self):
        resp = self.client.post("/dashboard/api/templates/import",
                                json={"template_id": "jira-worklog", "project_name": "Support"})
        self.assertEqual(resp.status_code, 200)

        row = self._one_row("template.import", "template")
        self.assertEqual(row.resource_id, "jira-worklog")
        details = row.get_details()
        self.assertEqual(details["project_id"], 7)
        self.assertEqual(details["agents_created"], 5)

    def test_a_failed_template_import_is_not_audited(self):
        self.server._import_template.return_value = {"error": "Template not found"}
        resp = self.client.post("/dashboard/api/templates/import",
                                json={"template_id": "missing"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._rows(), [])


class TestTemplateCreateAudit(ImportAuditTestCase):

    def setUp(self):
        super().setUp()
        self.server._create_template_from_agents = MagicMock(return_value={
            "success": True, "template_id": "my_template", "path": "/tmp/my_template.json",
        })
        self.body = {"project_id": 3, "root_agent": "support_root",
                     "template_id": "my/template", "category": "support"}

    def test_creating_a_template_writes_an_audit_row(self):
        resp = self.client.post("/dashboard/api/templates/create-from-agents", json=self.body)
        self.assertEqual(resp.status_code, 200)

        row = self._one_row("template.create", "template")
        # The sanitized id is what a later delete or import will name, so that is
        # the one worth recording — not the raw request value.
        self.assertEqual(row.resource_id, "my_template")
        details = row.get_details()
        self.assertEqual(details["project_id"], 3)
        self.assertEqual(details["root_agent"], "support_root")
        self.assertEqual(details["category"], "support")

    def test_a_failed_template_create_is_not_audited(self):
        self.server._create_template_from_agents.return_value = {"error": "No agents found"}
        resp = self.client.post("/dashboard/api/templates/create-from-agents", json=self.body)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._rows(), [])


class TestTemplateSyncAudit(ImportAuditTestCase):

    def setUp(self):
        super().setUp()
        self.server._sync_template = MagicMock(return_value={
            "success": True, "project_id": 4, "template_id": "jira-worklog",
            "synced_to_version": "3.0", "agents_added": 1, "agents_updated": 2,
            "memory_blocks_added": 0, "memory_blocks_updated": 1,
        })

    def test_syncing_a_template_writes_an_audit_row(self):
        resp = self.client.post("/dashboard/api/templates/sync", json={"project_id": 4})
        self.assertEqual(resp.status_code, 200)

        row = self._one_row("template.sync", "template")
        self.assertEqual(row.resource_id, "jira-worklog")
        details = row.get_details()
        self.assertEqual(details["synced_to_version"], "3.0")
        self.assertEqual(details["agents_updated"], 2)

    def test_a_failed_sync_is_not_audited(self):
        self.server._sync_template.return_value = {"error": "Project was not created from a template"}
        resp = self.client.post("/dashboard/api/templates/sync", json={"project_id": 4})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._rows(), [])


class TestAuditConstants(unittest.TestCase):

    def test_the_action_constants_the_call_sites_use_exist(self):
        self.assertEqual(audit_service.ACTION_AGENTS_IMPORT, "agents.import")
        self.assertEqual(audit_service.ACTION_TEMPLATE_CREATE, "template.create")
        self.assertEqual(audit_service.ACTION_TEMPLATE_SYNC, "template.sync")

    def test_template_import_is_no_longer_dead(self):
        # The constant existed from the start and nothing referenced it.
        self.assertEqual(audit_service.ACTION_TEMPLATE_IMPORT, "template.import")
        source = Path(__file__).resolve().parents[1] / "utils" / "dashboard" / "dashboard_server.py"
        self.assertIn("ACTION_TEMPLATE_IMPORT", source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
