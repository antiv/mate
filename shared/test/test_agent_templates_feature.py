#!/usr/bin/env python3
"""
Unit tests for loading agent templates, specifically verifying that
expose_as_model works when importing templates.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.template_service import TemplateService

class TestAgentTemplatesFeature(unittest.TestCase):

    def test_load_coding_agent_template(self):
        project_root = Path(__file__).parent.parent.parent
        template_service = TemplateService(project_root=project_root)
        
        template = template_service.get_template("coding-agent")
        self.assertIsNotNone(template)
        
        # Verify metadata
        meta = template.get("template_meta", {})
        self.assertEqual(meta.get("id"), "coding-agent")
        self.assertEqual(meta.get("category"), "code")
        self.assertEqual(meta.get("root_agent"), "coding_root")
        
        # Verify agents list
        agents = template.get("agents", [])
        self.assertEqual(len(agents), 3)
        
        # Root agent qwen coder details
        root_agent = next((a for a in agents if a["name"] == "coding_root"), None)
        self.assertIsNotNone(root_agent)
        self.assertEqual(root_agent.get("model_name"), "openrouter/qwen/qwen3-coder-next")
        self.assertTrue(root_agent.get("expose_as_model"))
        
        # Check subagents are NOT exposed
        tester_agent = next((a for a in agents if a["name"] == "coding_tester"), None)
        self.assertIsNotNone(tester_agent)
        self.assertFalse(tester_agent.get("expose_as_model", False))

    def test_load_murder_mystery_templates(self):
        # Serbian and English editions share the same structure
        for template_id in ("murder-mystery", "murder-mystery-en"):
            with self.subTest(template_id=template_id):
                self._check_murder_mystery_template(template_id)

    def _check_murder_mystery_template(self, template_id):
        project_root = Path(__file__).parent.parent.parent
        template_service = TemplateService(project_root=project_root)

        template = template_service.get_template(template_id)
        self.assertIsNotNone(template)

        # Verify metadata
        meta = template.get("template_meta", {})
        self.assertEqual(meta.get("id"), template_id)
        self.assertEqual(meta.get("category"), "demo")
        self.assertEqual(meta.get("root_agent"), "villa_gm_root")

        # Root + 4 suspects
        agents = template.get("agents", [])
        self.assertEqual(len(agents), 5)

        # Root reads case blocks via memory_blocks tools
        root_agent = next((a for a in agents if a["name"] == "villa_gm_root"), None)
        self.assertIsNotNone(root_agent)
        self.assertEqual(root_agent.get("parent_agents"), [])
        self.assertTrue(json.loads(root_agent["tool_config"]).get("memory_blocks"))

        # Suspects: children of root, no tools (secret isolation), valid guardrails
        suspects = [a for a in agents if a["name"] != "villa_gm_root"]
        models = set()
        for suspect in suspects:
            self.assertEqual(suspect.get("parent_agents"), ["villa_gm_root"])
            self.assertIsNone(suspect.get("tool_config"))
            models.add(suspect.get("model_name"))
            guardrails = json.loads(suspect["guardrail_config"])["guardrails"]
            types = {g["type"] for g in guardrails if g.get("enabled")}
            self.assertIn("prompt_injection", types)
            self.assertIn("content_policy", types)
        # Multi-LLM: every suspect on a different model
        self.assertEqual(len(models), 4)

        # Culprit additionally has a redact content policy
        culprit = next(a for a in agents if a["name"] == "villa_doktorka")
        actions = [g["action"] for g in json.loads(culprit["guardrail_config"])["guardrails"]]
        self.assertIn("redact", actions)

        # Case blocks present
        labels = {b["label"] for b in template.get("memory_blocks", [])}
        self.assertIn("case_dossier", labels)
        self.assertIn("case_solution", labels)
        self.assertTrue(any(l.startswith("evidence_") for l in labels))

    @patch('shared.utils.database_client.get_database_client')
    def test_import_template_expose_as_model(self, mock_get_db):
        from shared.utils.dashboard.dashboard_server import DashboardServer
        from fastapi import FastAPI
        
        project_root = Path(__file__).parent.parent.parent
        
        # Setup mocks
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        mock_session = MagicMock()
        mock_db.get_session.return_value = mock_session
        
        # Create dashboard server instance
        server = DashboardServer(app=FastAPI(), project_root=project_root)
        
        # Mock create_project and other utilities
        server._create_project = Mock(return_value={"id": 42})
        server._copy_template_agent = Mock()
        
        # Capture the agent config dicts sent to _create_agent_config
        created_configs = []
        def mock_create_agent_config(config_data, changed_by=None):
            created_configs.append(config_data)
            return True
            
        server._create_agent_config = Mock(side_effect=mock_create_agent_config)
        
        # Run import_template
        result = server._import_template("coding-agent", project_name="My Coding Team")
        
        # Verify successful import structure
        self.assertNotIn("error", result)
        self.assertEqual(len(created_configs), 3)
        
        # Find coding_root config
        root_cfg = next((c for c in created_configs if c["name"].endswith("_root")), None)
        self.assertIsNotNone(root_cfg)
        self.assertTrue(root_cfg.get("expose_as_model"))
        
        # Find coding_tester config (should not be exposed)
        tester_cfg = next((c for c in created_configs if c["name"].endswith("_tester")), None)
        self.assertIsNotNone(tester_cfg)
        self.assertFalse(tester_cfg.get("expose_as_model"))

if __name__ == '__main__':
    unittest.main()
