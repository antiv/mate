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
