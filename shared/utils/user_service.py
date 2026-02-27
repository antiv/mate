"""
User service for session-based user management and role-based access control.

This module handles:
- Creating users from session data
- Managing user roles
- Checking user permissions for agent access
"""

import logging
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .database_client import get_database_client
from .models import User

logger = logging.getLogger(__name__)


class UserService:
    """Service for managing users and their roles."""
    
    def __init__(self):
        self.db_client = get_database_client()
    
    def get_session(self) -> Optional[Session]:
        """Get database session."""
        return self.db_client.get_session()
    
    def get_or_create_user(self, user_id: str) -> Optional[User]:
        """
        Get existing user by user_id or create a new one with default 'user' role.
        
        Args:
            user_id: User identifier from the request
            
        Returns:
            User instance or None if database error
        """
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return None
        
        try:
            # Try to get existing user by user_id
            user = session.query(User).filter(User.user_id == user_id).first()
            
            if user:
                logger.debug(f"Found existing user {user_id}")
                return user
            
            # Create new user with default 'user' role
            new_user = User(
                user_id=user_id,
                roles='["user"]'  # Default role
            )
            
            session.add(new_user)
            session.commit()
            logger.info(f"Created new user {user_id} with default 'user' role")
            return new_user
            
        except IntegrityError as e:
            logger.error(f"Integrity error creating user {user_id}: {e}")
            session.rollback()
            # Try to get the user again in case of race condition
            try:
                user = session.query(User).filter(User.user_id == user_id).first()
                return user
            except Exception as get_error:
                logger.error(f"Error getting user after integrity error: {get_error}")
                return None
        except Exception as e:
            logger.error(f"Error creating/getting user {user_id}: {e}")
            session.rollback()
            return None
        finally:
            session.close()
    
    def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by user ID."""
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return None
        
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            return user
        except Exception as e:
            logger.error(f"Error getting user by ID {user_id}: {e}")
            return None
        finally:
            session.close()
    
    def update_user_roles(self, user_id: str, roles: List[str]) -> bool:
        """
        Update user roles.
        
        Args:
            user_id: User identifier
            roles: List of roles to set for the user
            
        Returns:
            True if successful, False otherwise
        """
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return False
        
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                logger.error(f"User not found: {user_id}")
                return False
            
            user.set_roles(roles)
            session.commit()
            logger.info(f"Updated roles for user {user_id}: {roles}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating roles for user {user_id}: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def check_user_access(self, user_id: str, allowed_roles: List[str]) -> bool:
        """
        Check if user has access based on their roles and allowed roles.
        
        Args:
            user_id: User identifier
            allowed_roles: List of roles that are allowed access
            
        Returns:
            True if user has access, False otherwise
        """
        # If no roles specified, allow access (backward compatibility)
        if not allowed_roles:
            return True
        
        user = self.get_user_by_id(user_id)
        if not user:
            logger.warning(f"User not found: {user_id}, denying access")
            return False
        
        user_roles = user.get_roles()
        
        # Check if user has any of the allowed roles
        has_access = any(role in user_roles for role in allowed_roles)
        
        if has_access:
            logger.debug(f"Access granted for user {user_id}. User roles: {user_roles}, Required: {allowed_roles}")
        else:
            logger.warning(f"Access denied for user {user_id}. User roles: {user_roles}, Required: {allowed_roles}")
        
        return has_access
    
    def get_user_roles(self, user_id: str) -> List[str]:
        """
        Get user roles by user ID.
        
        Args:
            user_id: User identifier
            
        Returns:
            List of user roles, defaults to ['user'] if user not found
        """
        user = self.get_user_by_id(user_id)
        if user:
            return user.get_roles()
        return ['user']  # Default role if user not found
    
    def get_user_profile(self, user_id: str) -> Optional[str]:
        """
        Get user profile data by user ID.
        
        Args:
            user_id: User identifier
            
        Returns:
            User profile data as text, or None if user not found or no profile
        """
        user = self.get_user_by_id(user_id)
        if user:
            return user.get_profile_data()
        return None
    
    def update_user_profile(self, user_id: str, profile_data: str) -> bool:
        """
        Update user profile data.
        
        Args:
            user_id: User identifier
            profile_data: Profile data as text to set
            
        Returns:
            True if successful, False otherwise
        """
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return False
        
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if not user:
                logger.error(f"User not found: {user_id}")
                return False
            
            user.set_profile_data(profile_data)
            session.commit()
            logger.info(f"Updated profile for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating profile for user {user_id}: {e}")
            session.rollback()
            return False
        finally:
            session.close()


# Global instance
_user_service = None

def get_user_service() -> UserService:
    """Get the global user service instance."""
    global _user_service
    if _user_service is None:
        _user_service = UserService()
    return _user_service
