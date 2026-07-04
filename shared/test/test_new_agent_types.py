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
    def test_initialize_graph_agent_with_conditional_routing(self, mock_create_model, mock_tool_factory):
        """Test GraphWorkflow with router node and route-conditional dict edges."""
        # Mock tool factory
        mock_tool_factory.return_value.create_tools.return_value = []

        planner_config = {
            'edges': [
                ['START', 'classifier'],
                {'from': 'classifier', 'to': 'intent_router'},
                {'from': 'intent_router', 'to': 'refund_agent', 'route': 'refund'},
                {'from': 'intent_router', 'to': ['faq_agent'], 'route': 'faq'}
            ],
            'router_nodes': [
                {
                    'name': 'intent_router',
                    'state_key': 'classifier_output',
                    'routes': ['refund', 'faq'],
                    'default_route': 'faq'
                }
            ]
        }
        config = {
            'name': 'test_graph_agent_routing',
            'type': 'graph',
            'description': 'Test graph agent with conditional routing',
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

        from google.adk.workflow import Node, Edge, FunctionNode
        mock_sub_agents = [
            Node(name='classifier'),
            Node(name='refund_agent'),
            Node(name='faq_agent')
        ]

        result = self.agent_manager._initialize_agent(config, mock_sub_agents)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_graph_agent_routing')
        # 3 subagents + 1 router node
        self.assertEqual(len(result.sub_agents), 4)
        self.assertEqual(result.sub_agents[3].name, 'intent_router')
        self.assertIsInstance(result.sub_agents[3], FunctionNode)
        # 1 chain edge + 3 Edge objects (single 'to' values plus one list target)
        self.assertEqual(len(result.edges), 4)
        routed_edges = [e for e in result.edges if isinstance(e, Edge) and e.route]
        self.assertEqual(
            sorted((e.route, e.to_node.name) for e in routed_edges),
            [('faq', 'faq_agent'), ('refund', 'refund_agent')]
        )

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_router_node_route_selection(self, mock_create_model, mock_tool_factory):
        """Test that a router node picks routes from state: exact, substring, and default."""
        mock_tool_factory.return_value.create_tools.return_value = []

        planner_config = {
            'edges': [
                ['START', 'classifier'],
                {'from': 'classifier', 'to': 'intent_router'},
                {'from': 'intent_router', 'to': 'refund_agent', 'route': 'refund'},
                {'from': 'intent_router', 'to': 'faq_agent', 'route': 'faq'}
            ],
            'router_nodes': [
                {
                    'name': 'intent_router',
                    'state_key': 'classifier',
                    'routes': ['refund', 'faq'],
                    'default_route': 'faq'
                }
            ]
        }
        config = {
            'name': 'test_router_selection',
            'type': 'graph',
            'description': 'Router selection test',
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

        from google.adk.workflow import Node
        mock_sub_agents = [
            Node(name='classifier'),
            Node(name='refund_agent'),
            Node(name='faq_agent')
        ]

        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        self.assertIsNotNone(result)
        router = next(sa for sa in result.sub_agents if sa.name == 'intent_router')
        router_fn = router._func

        # Exact match on state_key
        ctx = Mock()
        ctx.state = {'classifier': 'Refund'}
        router_fn(ctx)
        self.assertEqual(ctx.route, 'refund')

        # Substring match via "<state_key>_output" fallback
        ctx = Mock()
        ctx.state = {'classifier_output': 'The user intent is: FAQ question.'}
        router_fn(ctx)
        self.assertEqual(ctx.route, 'faq')

        # No match -> default route
        ctx = Mock()
        ctx.state = {'classifier': 'nonsense'}
        router_fn(ctx)
        self.assertEqual(ctx.route, 'faq')

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_graph_agent_retry_config(self, mock_create_model, mock_tool_factory):
        """Test workflow-level and per-node retry_config on graph agents."""
        mock_tool_factory.return_value.create_tools.return_value = []

        planner_config = {
            'edges': [['START', 'worker']],
            'retry_config': {'max_attempts': 4, 'backoff_factor': 3.0},
            'node_retry': {
                'worker': {'max_attempts': 2},
                'missing_node': {'max_attempts': 9}
            }
        }
        config = {
            'name': 'test_graph_retry',
            'type': 'graph',
            'description': 'Retry test',
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

        from google.adk.workflow import Node
        worker = Node(name='worker')
        result = self.agent_manager._initialize_agent(config, [worker])

        self.assertIsNotNone(result)
        self.assertIsNotNone(result.retry_config)
        self.assertEqual(result.retry_config.max_attempts, 4)
        self.assertEqual(result.retry_config.backoff_factor, 3.0)
        self.assertIsNotNone(worker.retry_config)
        self.assertEqual(worker.retry_config.max_attempts, 2)

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_llm_agent_retry_config(self, mock_create_model, mock_tool_factory):
        """Test retry_config on llm agents via planner_config."""
        mock_tool_factory.return_value.create_tools.return_value = []
        mock_create_model.return_value = 'gemini-2.5-flash'

        config = {
            'name': 'test_llm_retry',
            'type': 'llm',
            'description': 'Retry test',
            'instruction': 'Test instruction',
            'model_name': 'gemini-2.5-flash',
            'tool_config': None,
            'max_iterations': None,
            'allowed_for_roles': [],
            'planner_config': json.dumps({'retry_config': {'max_attempts': 3}})
        }

        result = self.agent_manager._initialize_agent(config, [])
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.retry_config)
        self.assertEqual(result.retry_config.max_attempts, 3)

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_router_node_requires_name_and_state_key(self, mock_create_model, mock_tool_factory):
        """Test that router_nodes without name/state_key fail agent initialization."""
        mock_tool_factory.return_value.create_tools.return_value = []

        planner_config = {
            'edges': [['START', 'classifier']],
            'router_nodes': [{'name': 'broken_router'}]
        }
        config = {
            'name': 'test_router_invalid',
            'type': 'graph',
            'description': 'Invalid router test',
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

        from google.adk.workflow import Node
        result = self.agent_manager._initialize_agent(config, [Node(name='classifier')])
        self.assertIsNone(result)
        self.assertIn('router_nodes entries require', self.agent_manager.last_error)

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_initialize_loop_agent(self, mock_create_model, mock_tool_factory):
        """Test initialization of loop agent with GraphWorkflow."""
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
        
        # Mock sub-agents
        from google.adk.workflow import Node
        mock_sub_agent_1 = Node(name='sub_agent_1')
        mock_sub_agents = [mock_sub_agent_1]
        
        # Call the method
        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_loop_agent')
        self.assertEqual(result.description, 'Test loop agent')
        self.assertEqual(len(result.sub_agents), 2)
        self.assertEqual(result.sub_agents[0].name, 'sub_agent_1')
        self.assertEqual(result.sub_agents[1].name, 'test_loop_agent_loop_condition')
        self.assertEqual(result.max_iterations, 10)
    
    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_initialize_loop_agent_default_iterations(self, mock_create_model, mock_tool_factory):
        """Test initialization of loop agent with default max_iterations."""
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
        from google.adk.workflow import Node
        mock_sub_agent_1 = Node(name='sub_agent_1')
        mock_sub_agents = [mock_sub_agent_1]
        
        # Call the method
        result = self.agent_manager._initialize_agent(config, mock_sub_agents)
        
        # Verify result
        self.assertIsNotNone(result)
        self.assertEqual(result.name, 'test_loop_agent_default')
        self.assertEqual(result.max_iterations, None)  # Default value
        self.assertEqual(len(result.sub_agents), 2)
        self.assertEqual(result.sub_agents[0].name, 'sub_agent_1')
        self.assertEqual(result.sub_agents[1].name, 'test_loop_agent_default_loop_condition')
    
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

    def test_initialize_agent_hierarchy_error_propagation(self):
        """Test that initialize_agent_hierarchy propagates configuration errors as ValueError."""
        # Mock database configuration retrieval
        mock_config = Mock(spec=AgentConfig)
        mock_config.name = 'test_error_root'
        mock_config.type = 'graph'
        mock_config.model_name = 'test-model'
        mock_config.description = 'Test graph agent'
        mock_config.instruction = 'Test instruction'
        mock_config.mcp_servers_config = None
        mock_config.tool_config = None
        mock_config.max_iterations = None
        mock_config.planner_config = None
        mock_config.generate_content_config = None
        mock_config.input_schema = None
        mock_config.output_schema = None
        mock_config.include_contents = None
        mock_config.guardrail_config = None
        mock_config.project_id = None
        mock_config.allowed_for_roles = None
        mock_config.get_parent_agents.return_value = []
        
        self.agent_manager.get_root_agent_by_name = Mock(return_value=mock_config)
        self.agent_manager.get_subagents = Mock(return_value=[])
        
        # Force a ValueError during graph compilation (e.g. no subagents configured)
        # Call the method and assert ValueError is raised with a descriptive message
        with self.assertRaises(ValueError) as context:
            self.agent_manager.initialize_agent_hierarchy('test_error_root')
            
        self.assertIn("No edges or subagents configured for graph agent test_error_root", str(context.exception))


if __name__ == '__main__':
    unittest.main()
