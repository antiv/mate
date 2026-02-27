"""
User profile tools for agents to read and update user profile data.

This module provides tools for agents to:
- Read user profile data for personalization
- Update user profile data when learning new information about the user
"""

import logging
from typing import Dict, Any, List, Optional
from google.adk.tools.tool_context import ToolContext
from ..user_service import get_user_service

logger = logging.getLogger(__name__)


def _get_user_id_from_context(tool_context: ToolContext) -> Optional[str]:
    """
    Extract user_id from tool context.
    
    Args:
        tool_context: Tool context
        
    Returns:
        user_id if found, None otherwise
    """
    if not tool_context:
        return None
    
    # Get user_id from invocation context (proper ADK way)
    user_id = None
    if hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        user_id = getattr(tool_context._invocation_context, 'user_id', None)
    if not user_id:
        user_id = getattr(tool_context, 'user_id', None)
    if not user_id and hasattr(tool_context, 'session') and tool_context.session:
        user_id = getattr(tool_context.session, 'user_id', None)
    
    return user_id


def get_user_profile(tool_context: ToolContext = None) -> str:
    """
    Get the current user's profile data.
    
    This tool allows the agent to retrieve the user's profile information
    for personalization purposes. The profile data is automatically retrieved
    for the current user based on the session context.
    
    Args:
        tool_context: Tool context (automatically provided by ADK)
    
    Returns:
        User profile data as text, or a message if no profile exists
    """
    try:
        user_id = _get_user_id_from_context(tool_context)
        if not user_id:
            return "Error: Unable to determine user ID from context"
        
        user_service = get_user_service()
        profile_data = user_service.get_user_profile(user_id)
        
        if profile_data:
            return f"User Profile:\n\n{profile_data}"
        else:
            return "No profile data available for this user. You can update the profile using update_user_profile tool when you learn information about the user."
    
    except Exception as e:
        logger.error(f"Error getting user profile: {e}", exc_info=True)
        return f"Error retrieving user profile: {str(e)}"


def update_user_profile(
    profile_data: str,
    tool_context: ToolContext = None
) -> str:
    """
    Update the current user's profile data AUTOMATICALLY without asking for permission.
    
    IMPORTANT: You MUST automatically update the user profile whenever you learn new information
    about the user. DO NOT ask for permission - just update it directly. The user expects you
    to remember information they share with you.
    
    Use this tool automatically when the user shares information about themselves that should
    be remembered for future conversations. This includes:
    - Personal information (name, occupation, location, education)
    - Technical skills and experience
    - Professional background
    - Preferences and interests
    - Goals and aspirations
    - Any other relevant information about the user
    
    The profile data should be formatted as clear, structured text that can be
    easily read by the agent in future conversations. Include ALL known information about
    the user, not just the new information.
    
    Args:
        profile_data: The complete profile data to save (as text). Should be well-formatted
                     and include ALL relevant information about the user, merging new information
                     with existing profile data.
        tool_context: Tool context (automatically provided by ADK)
    
    Returns:
        Success message or error description
    """
    try:
        user_id = _get_user_id_from_context(tool_context)
        if not user_id:
            return "Error: Unable to determine user ID from context"
        
        if not profile_data or not profile_data.strip():
            return "Error: Profile data cannot be empty"
        
        user_service = get_user_service()
        success = user_service.update_user_profile(user_id, profile_data.strip())
        
        if success:
            return f"Successfully updated user profile. The profile now contains:\n\n{profile_data.strip()}"
        else:
            return "Error: Failed to update user profile. Please try again."
    
    except Exception as e:
        logger.error(f"Error updating user profile: {e}", exc_info=True)
        return f"Error updating user profile: {str(e)}"


def create_user_profile_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create user profile tools for agent.
    
    Args:
        config: Agent configuration dictionary containing tool_config with user_profile config
    
    Returns:
        List of user profile tools (get_user_profile and update_user_profile)
    """
    tools = []
    
    try:
        # Parse tool_config to get user_profile configuration
        tool_config = config.get('tool_config')
        if not tool_config:
            return []
        
        if isinstance(tool_config, str):
            import json
            tool_config_dict = json.loads(tool_config)
        else:
            tool_config_dict = tool_config
        
        user_profile_config = tool_config_dict.get('user_profile')
        if not user_profile_config or not user_profile_config.get('enabled'):
            return []
        
        # Add both tools
        tools.append(get_user_profile)
        tools.append(update_user_profile)
        
        logger.info(f"Created user profile tools for agent {config.get('name', 'unknown')}")
        
    except Exception as e:
        logger.error(f"Error creating user profile tools: {e}", exc_info=True)
    
    return tools

