#!/usr/bin/env python3
"""
Unit tests for project deletion with programmatic cascading deletes.
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import (
    Base, Project, AgentConfig, AgentConfigVersion, FileSearchStore,
    AgentFileSearchStore, FileSearchDocument, MemoryBlock, WidgetApiKey,
    AgentTrigger, WizardSession
)
from shared.utils.dashboard.dashboard_server import DashboardServer


class TestProjectDeleteCascade(unittest.TestCase):

    def setUp(self):
        """Set up in-memory SQLite database and test fixtures."""
        self.engine = create_engine("sqlite:///:memory:")
        
        # Enforce foreign keys in SQLite to reproduce and verify constraints
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        # Mock DatabaseClient
        self.mock_db_client = MagicMock()
        self.mock_db_client.get_session.side_effect = lambda: self.Session()

        # Patch get_database_client to return our mock_db_client
        self.patcher = patch('shared.utils.database_client.get_database_client', return_value=self.mock_db_client)
        self.patcher.start()

        # Initialize DashboardServer (passing dummy values)
        from fastapi import FastAPI
        self.app = FastAPI()
        self.project_root = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        
        self.server = DashboardServer(self.app, self.project_root)

    def tearDown(self):
        """Stop patches and clean up."""
        self.patcher.stop()

    def test_delete_project_cascade(self):
        """Test that deleting a project cascades correctly to all related models."""
        session = self.Session()

        # 1. Create a project
        project = Project(name="Test Project", description="Test Description")
        session.add(project)
        session.commit()
        project_id = project.id

        # 2. Add dependent records
        # AgentConfig and AgentConfigVersion
        agent = AgentConfig(name="test_agent", type="llm", model_name="gemini-1.5-pro", project_id=project_id)
        session.add(agent)
        session.commit()
        
        version = AgentConfigVersion(agent_config_id=agent.id, version_number=1, config_snapshot="{}")
        session.add(version)

        # FileSearchStore, AgentFileSearchStore, and FileSearchDocument
        store = FileSearchStore(store_name="test_store_123", display_name="Test Store", project_id=project_id)
        session.add(store)
        session.commit()

        agent_store = AgentFileSearchStore(agent_name="test_agent", store_id=store.id)
        session.add(agent_store)

        doc = FileSearchDocument(store_id=store.id, document_name="test_doc.txt", status="completed")
        session.add(doc)

        # MemoryBlock
        memory = MemoryBlock(project_id=project_id, label="test_memory", value="test_value")
        session.add(memory)

        # WidgetApiKey
        widget_key = WidgetApiKey(api_key="test_key_123", project_id=project_id, agent_name="test_agent")
        session.add(widget_key)

        # AgentTrigger
        trigger = AgentTrigger(name="test_trigger", trigger_type="cron", agent_name="test_agent", project_id=project_id, prompt="test")
        session.add(trigger)

        # WizardSession (linked to project - should NOT be deleted, only disassociated)
        wizard = WizardSession(session_token="test_token_xyz", trial_project_id=project_id, status="provisioned")
        session.add(wizard)

        session.commit()

        # Verify records exist before deletion
        self.assertEqual(session.query(Project).count(), 1)
        self.assertEqual(session.query(AgentConfig).count(), 1)
        self.assertEqual(session.query(AgentConfigVersion).count(), 1)
        self.assertEqual(session.query(FileSearchStore).count(), 1)
        self.assertEqual(session.query(AgentFileSearchStore).count(), 1)
        self.assertEqual(session.query(FileSearchDocument).count(), 1)
        self.assertEqual(session.query(MemoryBlock).count(), 1)
        self.assertEqual(session.query(WidgetApiKey).count(), 1)
        self.assertEqual(session.query(AgentTrigger).count(), 1)
        self.assertEqual(session.query(WizardSession).count(), 1)
        
        # Verify WizardSession is linked
        db_wizard = session.query(WizardSession).first()
        self.assertEqual(db_wizard.trial_project_id, project_id)

        session.close()

        # 3. Call _delete_project - this should succeed without any IntegrityError!
        result = self.server._delete_project(project_id)
        self.assertTrue(result.get("success"))

        # 4. Verify cascade deletion results
        session = self.Session()
        self.assertEqual(session.query(Project).count(), 0)
        self.assertEqual(session.query(AgentConfig).count(), 0)
        self.assertEqual(session.query(AgentConfigVersion).count(), 0)
        self.assertEqual(session.query(FileSearchStore).count(), 0)
        self.assertEqual(session.query(AgentFileSearchStore).count(), 0)
        self.assertEqual(session.query(FileSearchDocument).count(), 0)
        self.assertEqual(session.query(MemoryBlock).count(), 0)
        self.assertEqual(session.query(WidgetApiKey).count(), 0)
        self.assertEqual(session.query(AgentTrigger).count(), 0)
        
        # WizardSession must NOT be deleted, but trial_project_id must be None
        self.assertEqual(session.query(WizardSession).count(), 1)
        db_wizard = session.query(WizardSession).first()
        self.assertIsNone(db_wizard.trial_project_id)
        session.close()


if __name__ == "__main__":
    unittest.main()
