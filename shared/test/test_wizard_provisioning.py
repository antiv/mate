#!/usr/bin/env python3
"""
Unit tests for the Agent Builder Wizard:
- _import_template with a pre-loaded template_dict + per-prospect substitutions
- WizardProvisioningService.provision_trial wiring (project/agent import + widget key)
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


WIZARD_TEMPLATE = {
    "template_meta": {
        "id": "wizard-tier1-website-support",
        "name": "Website Support",
        "version": "1.0",
        "root_agent": "wztier1_root",
        "agent_prefix": "wztier1",
    },
    "project": {"name": "Wizard Trial", "description": "trial"},
    "agents": [
        {
            "name": "wztier1_root",
            "type": "llm",
            "model_name": "openrouter/deepseek/deepseek-chat-v3.1",
            "description": "Website support agent",
            "instruction": "Support for {{SITE_URL}}. Notes: {{EXTRA_INSTRUCTIONS}}",
            "parent_agents": [],
            "tool_config": "{\"browser\": true}",
            "mcp_servers_config": None,
        }
    ],
    "memory_blocks": [],
}


class TestImportTemplateSubstitutions(unittest.TestCase):

    @patch('shared.utils.database_client.get_database_client')
    def test_import_with_dict_and_substitutions(self, mock_get_db):
        from shared.utils.dashboard.dashboard_server import DashboardServer
        from fastapi import FastAPI

        project_root = Path(__file__).parent.parent.parent
        mock_get_db.return_value = MagicMock()

        server = DashboardServer(app=FastAPI(), project_root=project_root)
        server._create_project = Mock(return_value={"id": 7})
        server._copy_template_agent = Mock()

        created = []
        server._create_agent_config = Mock(side_effect=lambda cfg, changed_by=None: created.append(cfg) or True)

        result = server._import_template(
            template_dict=WIZARD_TEMPLATE,
            substitutions={"SITE_URL": "https://shop.example", "EXTRA_INSTRUCTIONS": "Be nice"},
            project_name="trial_tier1_abc123",
            changed_by="wizard",
        )

        self.assertNotIn("error", result)
        # Root agent name is mapped via the project slug, and returned for widget-key creation.
        self.assertTrue(result["root_agent_name"].endswith("_root"))
        self.assertEqual(len(created), 1)
        instr = created[0]["instruction"]
        self.assertIn("https://shop.example", instr)
        self.assertIn("Be nice", instr)
        self.assertNotIn("{{SITE_URL}}", instr)


class TestWizardProvisioning(unittest.TestCase):

    def _make_dashboard(self, import_result):
        dash = MagicMock()
        dash.template_service.get_template.return_value = WIZARD_TEMPLATE
        dash._import_template.return_value = import_result
        # Session used to create the widget key + update the wizard session row.
        self.mock_session = MagicMock()
        self.mock_ws = MagicMock()
        self.mock_session.query.return_value.filter.return_value.first.return_value = self.mock_ws
        dash.db_client.get_session.return_value = self.mock_session
        return dash

    def test_provision_trial_creates_widget_key(self):
        from shared.utils.wizard.provisioning_service import WizardProvisioningService

        dash = self._make_dashboard({
            "project_id": 7,
            "root_agent_name": "trial_tier1_abc_root",
        })
        svc = WizardProvisioningService(dash)
        result = svc.provision_trial(
            tier="tier1",
            step_data={"site_url": "https://shop.example", "instructions": "Be nice"},
            session_token="tok123",
        )

        self.assertNotIn("error", result)
        self.assertTrue(result["widget_api_key"].startswith("wk_"))
        self.assertEqual(result["chat_url"], f"/widget/chat?key={result['widget_api_key']}")
        self.assertEqual(result["root_agent_name"], "trial_tier1_abc_root")

        # Substitutions were passed through to the import path.
        _, kwargs = dash._import_template.call_args
        self.assertEqual(kwargs["substitutions"]["SITE_URL"], "https://shop.example")

        # A widget key was added and the wizard session was marked provisioned.
        self.assertTrue(self.mock_session.add.called)
        self.assertEqual(self.mock_ws.status, "provisioned")
        self.assertEqual(self.mock_ws.trial_project_id, 7)

    def test_provision_non_provisionable_tier(self):
        from shared.utils.wizard.provisioning_service import WizardProvisioningService

        dash = self._make_dashboard({})
        svc = WizardProvisioningService(dash)
        result = svc.provision_trial(tier="tier4", step_data={}, session_token="tok")
        self.assertIn("error", result)


class TestWizardCleanup(unittest.TestCase):
    """TTL cleanup removes expired trials but keeps leads (in-memory SQLite)."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from shared.utils.models import Base

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        class _DB:
            def __init__(self, mk):
                self._mk = mk
            def get_session(self):
                return self._mk()

        self.db = _DB(self.Session)

    def _seed_expired_trial(self):
        from datetime import datetime, timedelta, timezone
        from shared.utils.models import (
            Project, AgentConfig, AgentConfigVersion, MemoryBlock,
            WidgetApiKey, WizardSession, WizardLead,
        )
        s = self.Session()
        p = Project(name="trial_tier1_CAFE", description="t")
        s.add(p); s.flush()
        agent = AgentConfig(name="trial_tier1_CAFE_root", type="llm", model_name="x", project_id=p.id)
        s.add(agent); s.flush()
        # Version snapshot + memory blocks must also be removed by cleanup.
        s.add(AgentConfigVersion(agent_config_id=agent.id, version_number=1, config_snapshot="{}"))
        s.add(MemoryBlock(project_id=p.id, label="site_page_1", value="content"))
        s.add(MemoryBlock(project_id=p.id, label="site_index", value="idx"))
        s.add(WidgetApiKey(api_key="wk_cafe", project_id=p.id, agent_name="trial_tier1_CAFE_root", is_active=True))
        s.add(WizardSession(session_token="tok_cafe", tier="tier1", status="provisioned",
                            trial_project_id=p.id, widget_api_key="wk_cafe",
                            root_agent_name="trial_tier1_CAFE_root",
                            expires_at=datetime.now(timezone.utc) - timedelta(days=2)))
        s.add(WizardLead(tier="tier1", email="keep@example.com", trial_project_id=p.id,
                         trial_widget_key="wk_cafe", status="new"))
        s.commit(); pid = p.id; s.close()
        return pid

    @patch("shared.utils.wizard.cleanup._reload_adk_agents", lambda: None)
    def test_cleanup_removes_trial_keeps_lead(self):
        from shared.utils.models import (
            Project, AgentConfig, AgentConfigVersion, MemoryBlock,
            WidgetApiKey, WizardSession, WizardLead,
        )
        import shared.utils.wizard.cleanup as cleanup

        pid = self._seed_expired_trial()
        with patch.object(cleanup, "get_database_client", return_value=self.db):
            with patch.object(cleanup, "_delete_agent_folder", lambda name: None):
                result = cleanup.cleanup_expired_trials(ttl_days=7)

        self.assertEqual(result["removed_projects"], 1)
        s = self.Session()
        try:
            self.assertIsNone(s.query(Project).filter(Project.id == pid).first())
            self.assertEqual(s.query(AgentConfig).filter(AgentConfig.project_id == pid).count(), 0)
            self.assertEqual(s.query(AgentConfigVersion).count(), 0)
            self.assertEqual(s.query(MemoryBlock).filter(MemoryBlock.project_id == pid).count(), 0)
            self.assertIsNone(s.query(WidgetApiKey).filter(WidgetApiKey.api_key == "wk_cafe").first())
            ws = s.query(WizardSession).filter(WizardSession.session_token == "tok_cafe").first()
            self.assertEqual(ws.status, "expired")
            self.assertIsNone(ws.trial_project_id)
            lead = s.query(WizardLead).filter(WizardLead.email == "keep@example.com").first()
            self.assertIsNotNone(lead)
            self.assertEqual(lead.trial_widget_key, "wk_cafe")
        finally:
            s.close()

    @patch("shared.utils.wizard.cleanup._reload_adk_agents", lambda: None)
    def test_release_trial_keeps_lead(self):
        """Archiving frees the trial (release_trial) immediately, but the lead survives."""
        from shared.utils.models import Project, AgentConfig, MemoryBlock, WidgetApiKey, WizardLead
        import shared.utils.wizard.cleanup as cleanup

        pid = self._seed_expired_trial()
        with patch.object(cleanup, "get_database_client", return_value=self.db):
            with patch.object(cleanup, "_delete_agent_folder", lambda name: None):
                result = cleanup.release_trial(pid)

        self.assertTrue(result.get("released"))
        s = self.Session()
        try:
            self.assertIsNone(s.query(Project).filter(Project.id == pid).first())
            self.assertEqual(s.query(AgentConfig).filter(AgentConfig.project_id == pid).count(), 0)
            self.assertEqual(s.query(MemoryBlock).filter(MemoryBlock.project_id == pid).count(), 0)
            self.assertIsNone(s.query(WidgetApiKey).filter(WidgetApiKey.api_key == "wk_cafe").first())
            self.assertIsNotNone(s.query(WizardLead).filter(WizardLead.email == "keep@example.com").first())
        finally:
            s.close()

        with patch.object(cleanup, "get_database_client", return_value=self.db):
            self.assertFalse(cleanup.release_trial(pid).get("released"))


if __name__ == "__main__":
    unittest.main()
