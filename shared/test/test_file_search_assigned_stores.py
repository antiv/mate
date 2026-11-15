#!/usr/bin/env python3
"""
File Search stores assigned to an agent in the dashboard (agent_file_search_stores)
become its `file_search` tool on both runtimes. The ADK runtime added them in
AgentManager; the LangGraph runtime built the agent from tool_config alone, so an
agent with stores assigned had no File Search there.
"""

import asyncio
import json
import unittest
import uuid
from unittest.mock import patch

from shared.utils.database_client import get_database_client
from shared.utils.models import (DEFAULT_WORKSPACE_ID, AgentConfig, AgentFileSearchStore,
                                 FileSearchStore, Project)
from shared.utils.tools.file_search_tools import add_assigned_stores


def _tool_names(tools):
    return {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}


class TestAssignedStores(unittest.TestCase):

    def setUp(self):
        self.db = get_database_client()
        suffix = uuid.uuid4().hex[:8]
        self.agent = f"fs_agent_{suffix}"
        self.store = f"fileSearchStores/assigned-{suffix}"
        session = self.db.get_session()
        try:
            project = Project(name=f"fs-project-{suffix}", workspace_id=DEFAULT_WORKSPACE_ID)
            session.add(project)
            session.flush()
            self.project_id = project.id
            session.add(AgentConfig(name=self.agent, type="llm", model_name="openai/gpt-test",
                                    instruction="Answer from the files.", project_id=project.id,
                                    tool_config=json.dumps({"google_search": True})))
            store = FileSearchStore(store_name=self.store, display_name="Assigned",
                                    project_id=project.id)
            session.add(store)
            session.flush()
            session.add(AgentFileSearchStore(agent_name=self.agent, store_id=store.id))
            session.commit()
        finally:
            session.close()
        self.addCleanup(self._clean)

    def _clean(self):
        session = self.db.get_session()
        try:
            session.query(AgentFileSearchStore).filter_by(agent_name=self.agent).delete()
            session.query(FileSearchStore).filter_by(store_name=self.store).delete()
            session.query(AgentConfig).filter_by(name=self.agent).delete()
            session.query(Project).filter_by(id=self.project_id).delete()
            session.commit()
        finally:
            session.close()

    def _config(self):
        session = self.db.get_session()
        try:
            return session.query(AgentConfig).filter_by(name=self.agent).first().to_dict()
        finally:
            session.close()

    def test_the_assigned_stores_join_the_tool_config(self):
        tool_config = {"google_search": True}
        result = add_assigned_stores(tool_config, self.agent)
        self.assertEqual(result, {"google_search": True,
                                  "file_search": {"enabled": True, "store_names": [self.store]}})
        self.assertEqual(tool_config, {"google_search": True})  # not changed in place

    def test_an_agent_without_stores_keeps_its_tool_config(self):
        tool_config = {"google_search": True}
        self.assertIs(add_assigned_stores(tool_config, "no_such_agent"), tool_config)

    def test_a_database_error_keeps_the_tool_config(self):
        tool_config = {"google_search": True}
        with patch("shared.utils.file_search_service.FileSearchService.get_stores_for_agent",
                   side_effect=RuntimeError("database gone")):
            self.assertIs(add_assigned_stores(tool_config, self.agent), tool_config)

    def test_the_langgraph_runtime_gives_the_agent_file_search(self):
        from shared.utils.langgraph.agent_builder import AgentBuilder
        tools = asyncio.run(AgentBuilder()._build_tools(self._config()))
        self.assertIn("query_file_search", _tool_names(tools))

    def test_the_adk_runtime_gives_the_agent_file_search(self):
        from shared.utils.agent_manager import AgentManager
        session = self.db.get_session()
        try:
            row = session.query(AgentConfig).filter_by(name=self.agent).first()
            agent = AgentManager().initialize_agent_from_config(row)
        finally:
            session.close()
        self.assertIn("query_file_search", _tool_names(agent.tools))

    def test_without_stores_neither_runtime_has_file_search(self):
        session = self.db.get_session()
        try:
            session.query(AgentFileSearchStore).filter_by(agent_name=self.agent).delete()
            session.commit()
            row = session.query(AgentConfig).filter_by(name=self.agent).first()
            from shared.utils.agent_manager import AgentManager
            adk_agent = AgentManager().initialize_agent_from_config(row)
        finally:
            session.close()
        from shared.utils.langgraph.agent_builder import AgentBuilder
        tools = asyncio.run(AgentBuilder()._build_tools(self._config()))
        self.assertNotIn("query_file_search", _tool_names(adk_agent.tools))
        self.assertNotIn("query_file_search", _tool_names(tools))


if __name__ == "__main__":
    unittest.main()
