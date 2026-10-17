#!/usr/bin/env python3
"""
A file search store is assigned only to agents of its own project.

assign_store_to_agent checked that the agent and the store existed, not that they
belonged to the same project, so one project's agent could be given another
project's documents.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.file_search_service import FileSearchService
from shared.utils.models import AgentConfig, AgentFileSearchStore, Base, FileSearchStore, Project


class TestAssignStoreProject(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        session = self.Session()
        session.add_all([Project(id=1, name="one"), Project(id=2, name="two")])
        session.add(AgentConfig(name="support", type="llm", project_id=1))
        session.add_all([
            FileSearchStore(store_name="fileSearchStores/own", display_name="own", project_id=1),
            FileSearchStore(store_name="fileSearchStores/other", display_name="other", project_id=2),
        ])
        session.commit()
        session.close()
        db_client = MagicMock()
        db_client.get_session.side_effect = lambda: self.Session()
        self.service = FileSearchService(db_client)

    def _assigned(self):
        session = self.Session()
        try:
            return [a.store_id for a in session.query(AgentFileSearchStore).filter_by(agent_name="support")]
        finally:
            session.close()

    def test_a_store_of_the_agents_project_is_assigned(self):
        self.assertTrue(self.service.assign_store_to_agent("support", "fileSearchStores/own"))
        self.assertEqual(len(self._assigned()), 1)

    def test_a_store_of_another_project_is_refused(self):
        self.assertFalse(self.service.assign_store_to_agent("support", "fileSearchStores/other"))
        self.assertEqual(self._assigned(), [])


if __name__ == "__main__":
    unittest.main()
