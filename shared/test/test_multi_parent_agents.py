"""
Test multi-parent agent functionality.

This test verifies that agents can have multiple parent agents and that
the agent tree is built correctly without duplicates.
"""

import unittest
import json
import tempfile
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add the parent directory to the path to import shared modules
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from utils.models import Base, AgentConfig, Project
from utils.agent_manager import AgentManager


class TestMultiParentAgents(unittest.TestCase):
    """Test cases for multi-parent agent functionality."""
    
    def setUp(self):
        """Set up test database and agent manager."""
        # Create in-memory SQLite database for testing
        self.engine = create_engine('sqlite:///:memory:', echo=False)
        Base.metadata.create_all(self.engine)
        
        # Create session factory
        Session = sessionmaker(bind=self.engine)
        self.session = Session()
        
        # Seed default project
        project = Project(name="Test Project", description="Project for unit tests")
        self.session.add(project)
        self.session.commit()
        self.project_id = project.id
        
        # Create agent manager
        self.agent_manager = AgentManager()
        
        # Create a mock database client
        class MockDBClient:
            def __init__(self, session):
                self.session = session
            
            def get_session(self):
                return self.session
        
        # Override the database client to use our test database
        self.agent_manager.db_client = MockDBClient(self.session)
    
    def tearDown(self):
        """Clean up test database."""
        self.session.close()
    
    def test_agent_with_multiple_parents(self):
        """Test that an agent can have multiple parent agents."""
        # Create test agents
        parent1 = AgentConfig(
            name="parent1",
            type="llm",
            description="First parent agent",
            project_id=self.project_id
        )
        
        parent2 = AgentConfig(
            name="parent2", 
            type="llm",
            description="Second parent agent",
            project_id=self.project_id
        )
        
        child = AgentConfig(
            name="child",
            type="llm",
            description="Child agent with multiple parents",
            parent_agents='["parent1", "parent2"]',
            project_id=self.project_id
        )
        
        # Add to database
        self.session.add(parent1)
        self.session.add(parent2)
        self.session.add(child)
        self.session.commit()
        
        # Test getting parent agents
        child_agents = self.session.query(AgentConfig).filter(AgentConfig.name == "child").first()
        parent_agents = child_agents.get_parent_agents()
        
        self.assertEqual(len(parent_agents), 2)
        self.assertIn("parent1", parent_agents)
        self.assertIn("parent2", parent_agents)
    
    def test_get_subagents_with_multiple_parents(self):
        """Test getting subagents when an agent has multiple parents."""
        # Create test agents
        parent1 = AgentConfig(
            name="parent1",
            type="llm",
            description="First parent agent",
            project_id=self.project_id
        )
        
        parent2 = AgentConfig(
            name="parent2",
            type="llm", 
            description="Second parent agent",
            project_id=self.project_id
        )
        
        child = AgentConfig(
            name="child",
            type="llm",
            description="Child agent with multiple parents",
            parent_agents='["parent1", "parent2"]',
            project_id=self.project_id
        )
        
        # Add to database
        self.session.add(parent1)
        self.session.add(parent2)
        self.session.add(child)
        self.session.commit()
        
        # Test getting subagents for parent1
        subagents_parent1 = self.agent_manager.get_subagents("parent1")
        self.assertEqual(len(subagents_parent1), 1)
        self.assertEqual(subagents_parent1[0].name, "child")
        
        # Test getting subagents for parent2
        subagents_parent2 = self.agent_manager.get_subagents("parent2")
        self.assertEqual(len(subagents_parent2), 1)
        self.assertEqual(subagents_parent2[0].name, "child")
    
    def test_agent_tree_building_with_multiple_parents(self):
        """Test that agent tree queries work correctly with multiple parent relationships."""
        # Create test agents
        root = AgentConfig(
            name="root",
            type="llm",
            description="Root agent",
            project_id=self.project_id
        )
        
        parent1 = AgentConfig(
            name="parent1",
            type="llm",
            description="First parent agent",
            parent_agents='["root"]',
            project_id=self.project_id
        )
        
        parent2 = AgentConfig(
            name="parent2",
            type="llm",
            description="Second parent agent", 
            parent_agents='["root"]',
            project_id=self.project_id
        )
        
        shared_child = AgentConfig(
            name="shared_child",
            type="llm",
            description="Child agent shared by both parents",
            parent_agents='["parent1", "parent2"]',
            project_id=self.project_id
        )
        
        # Add to database
        self.session.add(root)
        self.session.add(parent1)
        self.session.add(parent2)
        self.session.add(shared_child)
        self.session.commit()
        
        # Test that we can get the root agent
        root_config = self.agent_manager.get_root_agent_by_name("root")
        self.assertIsNotNone(root_config)
        self.assertEqual(root_config.name, "root")
        
        # Test that we can get subagents for root
        root_subagents = self.agent_manager.get_subagents("root")
        self.assertEqual(len(root_subagents), 2)  # parent1 and parent2
        subagent_names = [agent.name for agent in root_subagents]
        self.assertIn("parent1", subagent_names)
        self.assertIn("parent2", subagent_names)
        
        # Test that shared_child appears as subagent for both parent1 and parent2
        parent1_subagents = self.agent_manager.get_subagents("parent1")
        self.assertEqual(len(parent1_subagents), 1)
        self.assertEqual(parent1_subagents[0].name, "shared_child")
        
        parent2_subagents = self.agent_manager.get_subagents("parent2")
        self.assertEqual(len(parent2_subagents), 1)
        self.assertEqual(parent2_subagents[0].name, "shared_child")
        
        # Test getting parent agents for shared_child
        shared_child_parents = self.agent_manager.get_parent_agents("shared_child")
        self.assertEqual(len(shared_child_parents), 2)
        self.assertIn("parent1", shared_child_parents)
        self.assertIn("parent2", shared_child_parents)
    
    def test_parent_agent_methods(self):
        """Test the parent agent management methods."""
        # Create test agent
        agent = AgentConfig(
            name="test_agent",
            type="llm",
            description="Test agent",
            project_id=self.project_id
        )
        
        # Test adding parent agents
        agent.add_parent_agent("parent1")
        agent.add_parent_agent("parent2")
        
        parent_agents = agent.get_parent_agents()
        self.assertEqual(len(parent_agents), 2)
        self.assertIn("parent1", parent_agents)
        self.assertIn("parent2", parent_agents)
        
        # Test checking for parent agent
        self.assertTrue(agent.has_parent_agent("parent1"))
        self.assertTrue(agent.has_parent_agent("parent2"))
        self.assertFalse(agent.has_parent_agent("parent3"))
        
        # Test removing parent agent
        agent.remove_parent_agent("parent1")
        parent_agents = agent.get_parent_agents()
        self.assertEqual(len(parent_agents), 1)
        self.assertIn("parent2", parent_agents)
        self.assertNotIn("parent1", parent_agents)
        
        # Test setting parent agents
        agent.set_parent_agents(["parent3", "parent4"])
        parent_agents = agent.get_parent_agents()
        self.assertEqual(len(parent_agents), 2)
        self.assertIn("parent3", parent_agents)
        self.assertIn("parent4", parent_agents)
    
    def test_project_scoped_queries(self):
        """Ensure AgentManager filters agents by project."""
        other_project = Project(name="Secondary Project", description="Another project for testing")
        self.session.add(other_project)
        self.session.commit()
        
        primary_agent = AgentConfig(
            name="project_primary_agent",
            type="llm",
            description="Agent in primary project",
            project_id=self.project_id
        )
        secondary_agent = AgentConfig(
            name="project_secondary_agent",
            type="llm",
            description="Agent in secondary project",
            project_id=other_project.id
        )
        
        self.session.add(primary_agent)
        self.session.add(secondary_agent)
        self.session.commit()
        
        projects = self.agent_manager.get_projects()
        project_names = {project.name for project in projects}
        self.assertIn("Test Project", project_names)
        self.assertIn("Secondary Project", project_names)
        
        primary_agents = self.agent_manager.get_agents_by_project(self.project_id)
        secondary_agents = self.agent_manager.get_agents_by_project(other_project.id)
        
        self.assertEqual(len(primary_agents), 1)
        self.assertEqual(primary_agents[0].name, "project_primary_agent")
        
        self.assertEqual(len(secondary_agents), 1)
        self.assertEqual(secondary_agents[0].name, "project_secondary_agent")


if __name__ == '__main__':
    unittest.main()
