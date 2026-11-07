"""
RBAC (Role-Based Access Control) middleware for agent access control.

This module provides middleware functionality to check user permissions
before allowing access to agents based on their roles.
"""

import logging
from typing import Dict, Any, Optional
from .database_client import get_database_client
from .user_service import get_user_service
from .workspaces import SYSTEM_CALLERS, agent_workspace, caller_access

logger = logging.getLogger(__name__)


class RBACMiddleware:
    """Middleware for role-based access control."""
    
    def __init__(self):
        self.user_service = get_user_service()
    
    def check_agent_access(self, user_id: str, agent_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Check if user has access to an agent: a caller of the agent's workspace whose
        roles allow it (shared/utils/workspaces.py, caller_access).
        
        Args:
            user_id: User identifier from request
            agent_config: Agent configuration dictionary containing allowed_for_roles
            
        Returns:
            Tuple of (has_access: bool, error_message: Optional[str])
        """
        agent_name = agent_config.get('name', 'unknown')
        session = get_database_client().get_session()
        if not session:
            return False, "RBAC check failed: no database"
        try:
            workspace_id = agent_workspace(session, agent_name)
            # A new user joins the agent's workspace; system callers stay in the default
            user = self.user_service.get_or_create_user(
                user_id, None if user_id in SYSTEM_CALLERS else workspace_id)
            if not user:
                error_msg = "Failed to authenticate user"
                logger.error(f"RBAC: {error_msg} for user {user_id}")
                return False, error_msg

            # A new user's membership was committed by get_or_create_user's own session.
            # On MySQL (REPEATABLE READ) this transaction would still read as of its
            # first query and miss it: start a new one.
            session.rollback()
            access = caller_access(session, user, workspace_id)
            if access is None:
                # Says no more than an unknown agent would
                logger.warning(f"RBAC: Access denied for user {user_id} to agent {agent_name}: "
                               f"not a caller of its workspace ({workspace_id})")
                return False, "This agent is not available."
            passes_roles, roles = access
            if passes_roles:
                return True, None

            # Secure default: an agent with no roles configured is admin-only
            # (was previously open-to-all). Set explicit roles to grant wider access.
            allowed_roles = agent_config.get('allowed_for_roles', [])
            if not allowed_roles:
                error_msg = f"Access denied. This agent has no roles configured (admin-only). Your roles: {roles}"
                logger.warning(f"RBAC: Access denied for user {user_id} to agent {agent_name} (no roles configured; admin-only). User roles: {roles}")
                return False, error_msg

            if any(role in roles for role in allowed_roles):
                logger.debug(f"RBAC: Access granted for user {user_id} to agent {agent_name}")
                return True, None
            error_msg = f"Access denied. Required roles: {allowed_roles}, Your roles: {roles}"
            logger.warning(f"RBAC: Access denied for user {user_id} to agent {agent_name}. User roles: {roles}, Required: {allowed_roles}")
            return False, error_msg

        except Exception as e:
            error_msg = f"RBAC check failed: {str(e)}"
            logger.error(f"RBAC: Error checking access for user {user_id}: {e}")
            return False, error_msg
        finally:
            session.close()
    
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
