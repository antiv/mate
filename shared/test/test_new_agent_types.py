#!/usr/bin/env python3
"""
Unit tests for the agent types: GraphWorkflow and LoopAgent.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.agent_manager import AgentManager
from shared.utils.models import AgentConfig


class TestNewAgentTypes(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent_manager = AgentManager()
        
        # Mock database client
        self.mock_db_client = Mock()
        self.agent_manager.db_client = self.mock_db_client
        
        # Mock session
        self.mock_session = Mock()
        self.mock_db_client.get_session.return_value = self.mock_session
    
    def tearDown(self):
        """Clean up after tests."""
        self.agent_manager.clear_initialized_agents()
    
    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_initialize_graph_agent_default(self, mock_create_model, mock_tool_factory):
        """Test initialization of GraphWorkflow with default sequential fallback."""
        # Mock tool factory
        mock_tool_factory.return_value.create_tools.return_value = []
        
        # Create mock agent config without custom edges in planner_config
        config = {
            'name': 'test_graph_agent',
            'type': 'graph',
            'description': 'Test graph agent',
            'instruction': 'Test instruction',
            'model_name': 'test-model',
            'mcp_command': None,
            'mcp_args': None,
            'mcp_env': None,
            'tool_config': None,
            'max_iterations': None,
            'allowed_for_roles': [],
            'planner_config': None
        }
        
        # Mock sub-agents
        from google.adk.workflow import Node
        mock_sub_agent_1 = Node(name='sub_agent_1')
        mock_sub_agent_2 = Node(name='sub_agent_2')
        mock_sub_agents = [mock_sub_agent_1, mock_sub_agent_2]
        
        # Call the method
        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_graph_agent')
        self.assertEqual(result.description, 'Test graph agent')
        self.assertEqual(len(result.sub_agents), 2)
        self.assertEqual(result.sub_agents[0].name, 'sub_agent_1')
        self.assertEqual(result.sub_agents[1].name, 'sub_agent_2')
        self.assertEqual(len(result.edges), 1)  # Default sequential chain
    
    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_initialize_graph_agent_with_edges(self, mock_create_model, mock_tool_factory):
        """Test initialization of GraphWorkflow with custom edges configuration."""
        # Mock tool factory
        mock_tool_factory.return_value.create_tools.return_value = []
        
        # Create mock agent config with custom edges in planner_config
        planner_config = {
            'edges': [
                ['START', 'sub_agent_1'],
                ['sub_agent_1', 'sub_agent_2']
            ]
        }
        config = {
            'name': 'test_graph_agent_custom',
            'type': 'graph',
            'description': 'Test graph agent with custom edges',
            'instruction': 'Test instruction',
            'model_name': 'test-model',
            'mcp_command': None,
            'mcp_args': None,
            'mcp_env': None,
            'tool_config': None,
            'max_iterations': None,
            'allowed_for_roles': [],
            'planner_config': json.dumps(planner_config)
        }
        
        # Mock sub-agents
        from google.adk.workflow import Node
        mock_sub_agent_1 = Node(name='sub_agent_1')
        mock_sub_agent_2 = Node(name='sub_agent_2')
        mock_sub_agents = [mock_sub_agent_1, mock_sub_agent_2]
        
        # Call the method
        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_graph_agent_custom')
        self.assertEqual(len(result.sub_agents), 2)
        self.assertEqual(len(result.edges), 2)
    
    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_initialize_loop_agent(self, mock_create_model, mock_tool_factory):
        """Test initialization of LoopAgent."""
        # Mock tool factory
        mock_tool_factory.return_value.create_tools.return_value = []
        
        # Create mock agent config
        config = {
            'name': 'test_loop_agent',
            'type': 'loop',
            'description': 'Test loop agent',
            'instruction': 'Test instruction',
            'model_name': 'test-model',
            'mcp_command': None,
            'mcp_args': None,
            'mcp_env': None,
            'tool_config': None,
            'max_iterations': 10,
            'allowed_for_roles': []
        }
        
        # Mock sub-agents - use empty list for now to avoid validation issues
        mock_sub_agents = []
        
        # Call the method
        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_loop_agent')
        self.assertEqual(result.description, 'Test loop agent')
        self.assertEqual(len(result.sub_agents), 0)
        self.assertEqual(result.max_iterations, 10)
    
    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_initialize_loop_agent_default_iterations(self, mock_create_model, mock_tool_factory):
        """Test initialization of LoopAgent with default max_iterations."""
        # Mock tool factory
        mock_tool_factory.return_value.create_tools.return_value = []
        
        # Create mock agent config without max_iterations
        config = {
            'name': 'test_loop_agent_default',
            'type': 'loop',
            'description': 'Test loop agent with default iterations',
            'instruction': 'Test instruction',
            'model_name': 'test-model',
            'mcp_command': None,
            'mcp_args': None,
            'mcp_env': None,
            'tool_config': None,
            'max_iterations': None,
            'allowed_for_roles': []
        }
        
        # Mock sub-agents
        mock_sub_agents = []
        
        # Call the method
        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_loop_agent_default')
        self.assertEqual(result.max_iterations, None)  # Default value
    
    def test_initialize_agent_from_config_with_new_types(self):
        """Test initialize_agent_from_config with new agent types."""
        # Create mock agent config
        mock_config = Mock(spec=AgentConfig)
        mock_config.name = 'test_graph_agent'
        mock_config.type = 'graph'
        mock_config.model_name = 'test-model'
        mock_config.description = 'Test graph agent'
        mock_config.instruction = 'Test instruction'
        mock_config.mcp_command = None
        mock_config.mcp_args = None
        mock_config.mcp_env = None
        mock_config.tool_config = None
        mock_config.max_iterations = None
        mock_config.allowed_for_roles = None
        
        # Mock sub-agents
        mock_sub_agents = []
        
        with patch.object(self.agent_manager, '_initialize_agent') as mock_init:
            mock_init.return_value = Mock()
            
            result = self.agent_manager.initialize_agent_from_config(mock_config, mock_sub_agents)
            
            # Verify the method was called
            mock_init.assert_called_once()
            call_args = mock_init.call_args[0][0]  # First positional argument (config dict)
            
            # Verify the config dict contains the new fields
            self.assertEqual(call_args['name'], 'test_graph_agent')
            self.assertEqual(call_args['type'], 'graph')
            self.assertEqual(call_args['max_iterations'], None)
            
            # Verify result
            self.assertIsNotNone(result)
    
    def test_unknown_agent_type_error(self):
        """Test error handling for unknown agent types."""
        # Create mock agent config with unknown type
        mock_config = Mock(spec=AgentConfig)
        mock_config.name = 'test_unknown_agent'
        mock_config.type = 'unknown_type'
        mock_config.allowed_for_roles = None
        
        result = self.agent_manager.initialize_agent_from_config(mock_config)
        
        # Verify result is None for unknown type
        self.assertIsNone(result)
    
    def test_supported_agent_types(self):
        """Test that all supported agent types are recognized."""
        supported_types = ['llm', 'graph', 'loop']
        
        for agent_type in supported_types:
            with self.subTest(agent_type=agent_type):
                mock_config = Mock(spec=AgentConfig)
                mock_config.name = f'test_{agent_type}_agent'
                mock_config.type = agent_type
                mock_config.model_name = 'test-model'
                mock_config.description = f'Test {agent_type} agent'
                mock_config.instruction = 'Test instruction'
                mock_config.mcp_command = None
                mock_config.mcp_args = None
                mock_config.mcp_env = None
                mock_config.tool_config = None
                mock_config.max_iterations = None
                mock_config.allowed_for_roles = None
                
                with patch.object(self.agent_manager, '_initialize_agent') as mock_init:
                    mock_init.return_value = Mock()
                    
                    result = self.agent_manager.initialize_agent_from_config(mock_config)
                    
                    # Verify the method was called (not None)
                    self.assertIsNotNone(result)
                    mock_init.assert_called_once()


if __name__ == '__main__':
    unittest.main()
