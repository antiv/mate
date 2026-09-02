#!/usr/bin/env python3
"""
Tests that building a database agent with no description is logged (#79).

`description` is nullable on agents_config and nothing in the dashboard or the
API requires it, so an agent with an empty one is a normal reachable state. ADK
uses that field as the routing signal when a parent LLM agent picks a sub-agent
to delegate to, so an empty one degrades delegation silently: the agent is
constructed, appears in the tree, and is simply never routed to. These tests pin
the warning that makes it visible at startup instead of at delegation time.
"""

import unittest
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.agent_manager import AgentManager

LOGGER = 'shared.utils.agent_manager'


class TestEmptyDescriptionWarning(unittest.TestCase):

    def setUp(self):
        self.agent_manager = AgentManager()
        self.mock_db_client = Mock()
        self.agent_manager.db_client = self.mock_db_client
        self.mock_session = Mock()
        self.mock_db_client.get_session.return_value = self.mock_session

    def tearDown(self):
        self.agent_manager.clear_initialized_agents()

    def _config(self, **overrides):
        config = {
            'name': 'test_agent',
            'type': 'llm',
            'description': 'A useful description',
            'instruction': 'Test instruction',
            'model_name': 'test-model',
            'tool_config': None,
            'max_iterations': None,
            'allowed_for_roles': [],
            'planner_config': None,
        }
        config.update(overrides)
        return config

    def _warnings(self, records):
        return [r.getMessage() for r in records
                if r.levelname == 'WARNING' and 'has no description' in r.getMessage()]

    # -- warns ------------------------------------------------------------- #

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_agent_with_sub_agents_and_no_description_warns(self, mock_create_model, mock_tool_factory):
        mock_tool_factory.return_value.create_tools.return_value = []
        mock_create_model.return_value = 'gemini-2.0-flash'
        from google.adk.workflow import Node
        config = self._config(name='parent_agent', type='graph', description=None)

        with self.assertLogs(LOGGER, level='WARNING') as ctx:
            result = self.agent_manager._initialize_agent(config, [Node(name='child')])

        warnings = self._warnings(ctx.records)
        self.assertEqual(len(warnings), 1)
        self.assertIn('parent_agent', warnings[0])
        # The agent is still built — this is a warning, not a rejection.
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'parent_agent')

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_agent_that_is_itself_a_sub_agent_warns(self, mock_create_model, mock_tool_factory):
        mock_tool_factory.return_value.create_tools.return_value = []
        mock_create_model.return_value = 'gemini-2.0-flash'
        # No children of its own, but a parent needs a description to route to it.
        config = self._config(name='child_agent', description='', parent_agents=['parent_agent'])

        with self.assertLogs(LOGGER, level='WARNING') as ctx:
            result = self.agent_manager._initialize_agent(config, [])

        warnings = self._warnings(ctx.records)
        self.assertEqual(len(warnings), 1)
        self.assertIn('child_agent', warnings[0])
        self.assertIsNotNone(result)

    # -- stays quiet ------------------------------------------------------- #

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_lone_root_agent_with_no_description_does_not_warn(self, mock_create_model, mock_tool_factory):
        mock_tool_factory.return_value.create_tools.return_value = []
        mock_create_model.return_value = 'gemini-2.0-flash'
        # Nothing delegates to it and it delegates nowhere, so there is no
        # routing decision to degrade. Warning here would be noise on every start.
        config = self._config(name='lone_agent', description=None, parent_agents=[])

        with self.assertLogs(LOGGER, level='DEBUG') as ctx:
            self.agent_manager._initialize_agent(config, [])

        self.assertEqual(self._warnings(ctx.records), [])

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_agent_with_a_description_does_not_warn(self, mock_create_model, mock_tool_factory):
        mock_tool_factory.return_value.create_tools.return_value = []
        mock_create_model.return_value = 'gemini-2.0-flash'
        config = self._config(name='described_agent', parent_agents=['parent_agent'])

        with self.assertLogs(LOGGER, level='DEBUG') as ctx:
            result = self.agent_manager._initialize_agent(config, [])

        self.assertEqual(self._warnings(ctx.records), [])
        self.assertEqual(result.description, 'A useful description')


if __name__ == '__main__':
    unittest.main()
