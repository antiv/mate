"""
Agent MCP Manager
Manages dynamic creation of MCP servers for agents from database configuration
"""

import os
import logging
from typing import Dict, List, Optional
from fastapi import FastAPI
from .agent_mcp_server import AgentMCPServer
from ..database_client import get_database_client
from ..models import AgentConfig

logger = logging.getLogger(__name__)


class AgentMCPManager:
    """Manages dynamic agent MCP server creation"""
    
    def __init__(self, app: FastAPI):
        self.app = app
        self.db_client = get_database_client()
        self.agent_mcp_servers: Dict[str, AgentMCPServer] = {}
        self.enabled_agents = self._get_enabled_agents_from_env()
    
    def _get_enabled_agents_from_env(self) -> List[str]:
        """Get list of agent names to expose as MCPs from environment variable"""
        env_value = os.getenv("MCP_EXPOSED_AGENTS", "chess_mate_root")
        if not env_value:
            return []
        
        # Support comma-separated or space-separated list
        agents = [agent.strip() for agent in env_value.replace(",", " ").split() if agent.strip()]
        logger.info(f"MCP exposed agents from env: {agents}")
        return agents
    
    def initialize_agent_mcp_servers(self):
        """Initialize MCP servers for all enabled agents from database"""
        if not self.enabled_agents:
            logger.info("No agents configured for MCP exposure (MCP_EXPOSED_AGENTS not set)")
            print("⚠️  No agents configured for MCP exposure. Set MCP_EXPOSED_AGENTS environment variable.")
            return
        
        print(f"🔍 Looking for agents to expose as MCP: {self.enabled_agents}")
        
        session = self.db_client.get_session()
        if not session:
            logger.error("Failed to get database session for agent MCP initialization")
            print("❌ Failed to get database session for agent MCP initialization")
            return
        
        try:
            # Get all agents that should be exposed
            agents = session.query(AgentConfig).filter(
                AgentConfig.name.in_(self.enabled_agents),
                AgentConfig.disabled == False
            ).all()
            
            logger.info(f"Found {len(agents)} agents to expose as MCP servers")
            print(f"🔍 Found {len(agents)} agents in database matching: {self.enabled_agents}")
            
            if len(agents) == 0:
                print(f"⚠️  No agents found in database matching: {self.enabled_agents}")
                print("   Make sure agent names match exactly (case-sensitive)")
                # List all available agents for debugging
                all_agents = session.query(AgentConfig).filter(AgentConfig.disabled == False).all()
                if all_agents:
                    print(f"   Available agents: {[a.name for a in all_agents]}")
            
            for agent_config in agents:
                try:
                    agent_name = agent_config.name
                    agent_description = agent_config.description or f"MCP server for {agent_name} agent"
                    
                    print(f"🔧 Creating MCP server for agent: {agent_name}")
                    
                    # Create MCP server for this agent
                    mcp_server = AgentMCPServer(
                        self.app,
                        agent_name,
                        agent_description
                    )
                    
                    self.agent_mcp_servers[agent_name] = mcp_server
                    logger.info(f"✅ Created MCP server for agent: {agent_name}")
                    print(f"✅ Created MCP server for agent: {agent_name} at /agents/{agent_name}/mcp")
                    
                except Exception as e:
                    logger.error(f"Failed to create MCP server for agent {agent_config.name}: {e}", exc_info=True)
                    print(f"❌ Failed to create MCP server for agent {agent_config.name}: {e}")
                    import traceback
                    traceback.print_exc()
            
            print(f"✅ Initialized {len(self.agent_mcp_servers)} agent MCP servers")
            if len(self.agent_mcp_servers) > 0:
                print(f"   Available agent MCP endpoints:")
                for agent_name in self.agent_mcp_servers.keys():
                    print(f"   - /agents/{agent_name}/mcp")
                
                # Verify routes are registered
                self.verify_routes_registered()
            
        except Exception as e:
            logger.error(f"Error initializing agent MCP servers: {e}", exc_info=True)
            print(f"❌ Error initializing agent MCP servers: {e}")
            import traceback
            traceback.print_exc()
        finally:
            session.close()
    
    def get_agent_mcp_server(self, agent_name: str) -> Optional[AgentMCPServer]:
        """Get MCP server for a specific agent"""
        return self.agent_mcp_servers.get(agent_name)
    
    def list_agent_mcp_servers(self) -> List[str]:
        """List all agent names with MCP servers"""
        return list(self.agent_mcp_servers.keys())
    
    def verify_routes_registered(self):
        """Verify that routes are actually registered in the FastAPI app"""
        registered_paths = []
        for route in self.app.routes:
            if hasattr(route, 'path') and '/agents/' in route.path and '/mcp' in route.path:
                registered_paths.append(route.path)
        
        print(f"🔍 Verified {len(registered_paths)} agent MCP routes registered:")
        for path in sorted(set(registered_paths)):
            print(f"   - {path}")
        
        return len(registered_paths) > 0
