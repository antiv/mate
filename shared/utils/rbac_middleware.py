"""
RBAC (Role-Based Access Control) middleware for agent access control.

This module provides middleware functionality to check user permissions
before allowing access to agents based on their roles.
"""

import logging
from typing import Dict, Any, Optional
from .user_service import get_user_service

logger = logging.getLogger(__name__)


class RBACMiddleware:
    """Middleware for role-based access control."""
    
    def __init__(self):
        self.user_service = get_user_service()
    
    def check_agent_access(self, user_id: str, agent_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Check if user has access to an agent based on their roles.
        
        Args:
            user_id: User identifier from request
            agent_config: Agent configuration dictionary containing allowed_for_roles
            
        Returns:
            Tuple of (has_access: bool, error_message: Optional[str])
        """
        try:
            # Get or create user
            user = self.user_service.get_or_create_user(user_id)
            if not user:
                error_msg = "Failed to authenticate user"
                logger.error(f"RBAC: {error_msg} for user {user_id}")
                return False, error_msg
            
            # Get allowed roles for the agent
            allowed_roles = agent_config.get('allowed_for_roles', [])
            
            # If no roles specified, allow access (backward compatibility)
            if not allowed_roles:
                logger.debug(f"RBAC: No role restrictions for agent {agent_config.get('name', 'unknown')}, allowing access")
                return True, None
            
            # Check user access
            has_access = self.user_service.check_user_access(user_id, allowed_roles)
            
            if has_access:
                logger.debug(f"RBAC: Access granted for user {user_id} to agent {agent_config.get('name', 'unknown')}")
                return True, None
            else:
                user_roles = user.get_roles()
                error_msg = f"Access denied. Required roles: {allowed_roles}, Your roles: {user_roles}"
                logger.warning(f"RBAC: Access denied for user {user_id} to agent {agent_config.get('name', 'unknown')}. User roles: {user_roles}, Required: {allowed_roles}")
                return False, error_msg
                
        except Exception as e:
            error_msg = f"RBAC check failed: {str(e)}"
            logger.error(f"RBAC: Error checking access for user {user_id}: {e}")
            return False, error_msg
    
    def get_user_roles(self, user_id: str) -> list:
        """Get user roles for a user."""
        return self.user_service.get_user_roles(user_id)
    
    def update_user_roles(self, user_id: str, roles: list) -> bool:
        """Update user roles for a user."""
        return self.user_service.update_user_roles(user_id, roles)


# Global instance
_rbac_middleware = None

def get_rbac_middleware() -> RBACMiddleware:
    """Get the global RBAC middleware instance."""
    global _rbac_middleware
    if _rbac_middleware is None:
        _rbac_middleware = RBACMiddleware()
    return _rbac_middleware


class AccessDeniedException(Exception):
    """Exception raised when access is denied due to insufficient permissions."""
    
    def __init__(self, message: str, required_roles: list = None, user_roles: list = None):
        self.message = message
        self.required_roles = required_roles or []
        self.user_roles = user_roles or []
        super().__init__(self.message)
