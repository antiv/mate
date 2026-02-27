#!/usr/bin/env python3
"""
Simplified unit tests for the AgentManager class focusing on core functionality.
"""

import unittest
from unittest.mock import Mock, patch
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.agent_manager import AgentManager
from shared.utils.models import AgentConfig


class TestAgentManagerSimple(unittest.TestCase):
    
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
    
    def test_init(self):
        """Test AgentManager initialization."""
        manager = AgentManager()
        self.assertIsNotNone(manager.db_client)
        self.assertEqual(manager.initialized_agents, {})
    
    def test_get_session_success(self):
        """Test successful database session retrieval."""
        session = self.agent_manager.get_session()
        self.assertEqual(session, self.mock_session)
        self.mock_db_client.get_session.assert_called_once()
    
    def test_get_session_failure(self):
        """Test database session retrieval failure."""
        self.mock_db_client.get_session.return_value = None
        session = self.agent_manager.get_session()
        self.assertIsNone(session)
    
    def test_get_root_agent_by_name_success(self):
        """Test successful root agent retrieval."""
        # Mock agent config
        mock_agent = Mock(spec=AgentConfig)
        mock_agent.name = "test_root_agent"
        mock_agent.type = "root"
        mock_agent.disabled = False
        
        # Mock query
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = mock_agent
        self.mock_session.query.return_value = mock_query
        
        result = self.agent_manager.get_root_agent_by_name("test_root_agent")
        
        self.assertEqual(result, mock_agent)
        self.mock_session.query.assert_called_once_with(AgentConfig)
        self.mock_session.close.assert_called_once()
    
    def test_get_root_agent_by_name_not_found(self):
        """Test root agent retrieval when agent not found."""
        # Mock query returning None
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = None
        self.mock_session.query.return_value = mock_query
        
        result = self.agent_manager.get_root_agent_by_name("nonexistent_agent")
        
        self.assertIsNone(result)
        self.mock_session.close.assert_called_once()
    
    def test_get_subagents_success(self):
        """Test successful subagent retrieval."""
        # Mock subagent configs with proper get_parent_agents method
        mock_subagent1 = Mock(spec=AgentConfig)
        mock_subagent1.name = "subagent1"
        mock_subagent1.parent_agents = '["parent_agent"]'
        mock_subagent1.disabled = False
        # Use lambda to ensure it returns a list when called
        mock_subagent1.get_parent_agents = lambda: ["parent_agent"]
        
        mock_subagent2 = Mock(spec=AgentConfig)
        mock_subagent2.name = "subagent2"
        mock_subagent2.parent_agents = '["parent_agent"]'
        mock_subagent2.disabled = False
        # Use lambda to ensure it returns a list when called
        mock_subagent2.get_parent_agents = lambda: ["parent_agent"]
        
        # Mock query filter chain - filter() is called with two arguments
        mock_filter_result = Mock()
        mock_filter_result.all.return_value = [mock_subagent1, mock_subagent2]
        mock_query = Mock()
        # filter() is called once with two filter conditions
        mock_query.filter.return_value = mock_filter_result
        self.mock_session.query.return_value = mock_query
        
        result = self.agent_manager.get_subagents("parent_agent")
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], mock_subagent1)
        self.assertEqual(result[1], mock_subagent2)
        self.mock_session.close.assert_called_once()
    
    def test_add_hardcoded_subagents(self):
        """Test adding hardcoded subagents to parent agent."""
        # Mock parent agent
        mock_parent = Mock()
        mock_parent.sub_agents = []
        mock_parent.name = "parent_agent"
        
        # Mock hardcoded agents
        mock_hardcoded1 = Mock()
        mock_hardcoded2 = Mock()
        hardcoded_agents = [mock_hardcoded1, mock_hardcoded2]
        
        self.agent_manager._add_hardcoded_subagents(mock_parent, hardcoded_agents)
        
        self.assertEqual(len(mock_parent.sub_agents), 2)
        self.assertIn(mock_hardcoded1, mock_parent.sub_agents)
        self.assertIn(mock_hardcoded2, mock_parent.sub_agents)
    
    def test_get_initialized_agent(self):
        """Test getting initialized agent."""
        mock_agent = Mock()
        self.agent_manager.initialized_agents['test_agent'] = mock_agent
        
        result = self.agent_manager.get_initialized_agent('test_agent')
        
        self.assertEqual(result, mock_agent)
    
    def test_get_initialized_agent_not_found(self):
        """Test getting initialized agent that doesn't exist."""
        result = self.agent_manager.get_initialized_agent('nonexistent_agent')
        
        self.assertIsNone(result)
    
    def test_get_all_initialized_agents(self):
        """Test getting all initialized agents."""
        mock_agent1 = Mock()
        mock_agent2 = Mock()
        self.agent_manager.initialized_agents = {
            'agent1': mock_agent1,
            'agent2': mock_agent2
        }
        
        result = self.agent_manager.get_all_initialized_agents()
        
        self.assertEqual(result, {'agent1': mock_agent1, 'agent2': mock_agent2})
    
    def test_clear_initialized_agents(self):
        """Test clearing initialized agents."""
        # Add some agents
        self.agent_manager.initialized_agents['agent1'] = Mock()
        self.agent_manager.initialized_agents['agent2'] = Mock()
        
        # Clear agents
        self.agent_manager.clear_initialized_agents()
        
        self.assertEqual(self.agent_manager.initialized_agents, {})


if __name__ == '__main__':
    unittest.main()
