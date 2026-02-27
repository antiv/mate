"""
RBAC-aware agent factory for creating agents with role-based access control.

This module provides functions to create agents with RBAC enforcement,
typically used by web servers or APIs that have access to session information.
"""

import logging
from typing import Optional, Any, List
from .agent_manager import get_agent_manager
from .rbac_middleware import get_rbac_middleware, AccessDeniedException

logger = logging.getLogger(__name__)


class RBACAgentFactory:
    """Factory for creating agents with RBAC enforcement."""
    
    def __init__(self):
        self.agent_manager = get_agent_manager()
        self.rbac_middleware = get_rbac_middleware()
    
    def create_agent_with_rbac(self, agent_name: str, user_id: str, 
                              hardcoded_agents: Optional[List[Any]] = None) -> Optional[Any]:
        """
        Create an agent hierarchy with RBAC enforcement.
        
        Args:
            agent_name: Name of the root agent to create
            user_id: User identifier for RBAC check
            hardcoded_agents: Optional list of hardcoded agents to include
            
        Returns:
            Initialized agent with RBAC-filtered subagents, or None if access denied
            
        Raises:
            AccessDeniedException: If user doesn't have access to the root agent
        """
        try:
            logger.info(f"Creating agent {agent_name} with RBAC for user {user_id}")
            
            # Create user if not exists (will default to 'user' role)
            user = self.rbac_middleware.user_service.get_or_create_user(user_id)
            if not user:
                logger.error(f"Failed to create/get user {user_id}")
                return None
            
            logger.info(f"User roles for {user_id}: {user.get_roles()}")
            
            # Initialize agent hierarchy using the new tree builder (RBAC is now handled by callbacks)
            agent = self.agent_manager.build_agent_tree(
                agent_name, 
                hardcoded_agents
            )
            
            if agent:
                logger.info(f"Successfully created agent {agent_name} with RBAC for user {user_id}")
            else:
                logger.warning(f"Failed to create agent {agent_name} for user {user_id}")
            
            return agent
            
        except AccessDeniedException as e:
            logger.warning(f"Access denied creating agent {agent_name} for user {user_id}: {e.message}")
            raise
        except Exception as e:
            logger.error(f"Error creating agent {agent_name} with RBAC for user {user_id}: {e}")
            return None
    
    def get_user_roles(self, user_id: str) -> List[str]:
        """Get user roles for a user."""
        return self.rbac_middleware.get_user_roles(user_id)
    
    def update_user_roles(self, user_id: str, roles: List[str]) -> bool:
        """Update user roles for a user."""
        return self.rbac_middleware.update_user_roles(user_id, roles)
    
    def check_agent_access(self, user_id: str, agent_name: str) -> tuple[bool, Optional[str]]:
        """
        Check if user has access to a specific agent.
        
        Args:
            user_id: User identifier
            agent_name: Name of the agent to check access for
            
        Returns:
            Tuple of (has_access: bool, error_message: Optional[str])
        """
        try:
            # Get agent configuration
            agent_config = self.agent_manager.get_root_agent_by_name(agent_name)
            if not agent_config:
                return False, f"Agent {agent_name} not found"
            
            # Convert to dict for RBAC check
            config_dict = {
                'name': agent_config.name,
                'allowed_for_roles': []
            }
            
            # Parse allowed roles if present
            if agent_config.allowed_for_roles:
                try:
                    import json
                    config_dict['allowed_for_roles'] = json.loads(agent_config.allowed_for_roles)
                except json.JSONDecodeError:
                    pass  # Use empty list as default
            
            return self.rbac_middleware.check_agent_access(user_id, config_dict)
            
        except Exception as e:
            logger.error(f"Error checking access to agent {agent_name} for user {user_id}: {e}")
            return False, str(e)


# Global instance
_rbac_agent_factory = None

def get_rbac_agent_factory() -> RBACAgentFactory:
    """Get the global RBAC agent factory instance."""
    global _rbac_agent_factory
    if _rbac_agent_factory is None:
        _rbac_agent_factory = RBACAgentFactory()
    return _rbac_agent_factory


def create_agent_with_user(agent_name: str, user_id: str, 
                          hardcoded_agents: Optional[List[Any]] = None) -> Optional[Any]:
    """
    Convenience function to create an agent with RBAC using user ID.
    
    Args:
        agent_name: Name of the root agent to create
        user_id: User identifier for RBAC check
        hardcoded_agents: Optional list of hardcoded agents to include
        
    Returns:
        Initialized agent with RBAC-filtered subagents, or None if access denied
        
    Raises:
        AccessDeniedException: If user doesn't have access to the root agent
    """
    factory = get_rbac_agent_factory()
    return factory.create_agent_with_rbac(agent_name, user_id, hardcoded_agents)
