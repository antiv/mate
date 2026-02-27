"""
Tool Factory for creating and managing agent tools.

This factory provides centralized creation of various tool types including
MCP tools, Google services, and custom functions.
"""

import json
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class ToolFactory:
    """Factory class for creating agent tools based on configuration."""
    
    def __init__(self):
        """Initialize the tool factory."""
        self._tool_creators = {
            'mcp': self._create_mcp_tools,
            'google_search': self._create_google_search_tools,
            'google_drive': self._create_google_drive_tools,
            'cv_tools': self._create_cv_tools,
            'image_tools': self._create_image_tools,
            'supabase_storage': self._create_supabase_storage_tools,
            'custom_functions': self._create_custom_function_tools,
            'memory_blocks': self._create_memory_blocks_tools,
            'file_search': self._create_file_search_tools,
            'user_profile': self._create_user_profile_tools
        }
    
    def create_tools(self, config: Dict[str, Any]) -> List[Any]:
        """
        Create tools based on agent configuration.
        
        Args:
            config: Agent configuration dictionary containing:
                - mcp_command: Command for MCP server (e.g., 'npx')
                - mcp_args: JSON array of command arguments
                - mcp_env: JSON object of environment variables
                - tool_config: JSON string with tool configuration
        
        Returns:
            List of created tools
        """
        tools = []
        
        # Create MCP tools if configured
        if config.get('mcp_servers_config'):
            mcp_tools = self._create_mcp_tools(config)
            tools.extend(mcp_tools)
        
        # Parse tool_config for additional tools
        tool_config = config.get('tool_config')
        if tool_config:
            try:
                tool_config_dict = json.loads(tool_config)
                
                for tool_type, tool_config_value in tool_config_dict.items():
                    if tool_type in self._tool_creators and tool_config_value is not None:
                        creator = self._tool_creators[tool_type]
                        if tool_type == 'custom_functions':
                            # Custom functions expect a list and config
                            created_tools = creator(tool_config_value, config)
                        else:
                            # Other tools expect boolean config
                            created_tools = creator(config)
                        tools.extend(created_tools)
                    elif tool_type == 'create_agent' and tool_config_value is not None:
                        # Handle create_agent, delete_agent, update_agent, and read_agent tools separately
                        try:
                            from .create_agent_tool import create_agent, delete_agent, update_agent, read_agent
                            # Check if already added to avoid duplicates
                            existing_tool_names = {getattr(t, "__name__", str(t)) for t in tools}
                            if "create_agent" not in existing_tool_names:
                                tools.append(create_agent)
                                logger.debug(f"Added create_agent tool to agent {config.get('name', 'unknown')}")
                            if "delete_agent" not in existing_tool_names:
                                tools.append(delete_agent)
                                logger.debug(f"Added delete_agent tool to agent {config.get('name', 'unknown')}")
                            if "update_agent" not in existing_tool_names:
                                tools.append(update_agent)
                                logger.debug(f"Added update_agent tool to agent {config.get('name', 'unknown')}")
                            if "read_agent" not in existing_tool_names:
                                tools.append(read_agent)
                                logger.debug(f"Added read_agent tool to agent {config.get('name', 'unknown')}")
                        except Exception as e:
                            logger.warning(f"Failed to add agent management tools: {e}")
                        
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in tool_config for agent {config.get('name', 'unknown')}")
        
        # Automatically add user_profile update tool to all agents
        # Note: get_user_profile is not needed since profile is automatically injected in context
        # but we keep update_user_profile for agents to save new information
        existing_tool_names = {getattr(t, "__name__", str(t)) for t in tools}
        if "update_user_profile" not in existing_tool_names:
            try:
                from .user_profile_tools import update_user_profile
                tools.append(update_user_profile)
                logger.debug(f"Automatically added update_user_profile tool to agent {config.get('name', 'unknown')}")
            except Exception as e:
                logger.warning(f"Failed to automatically add update_user_profile tool: {e}")
        
        # Also add get_user_profile if not present (for edge cases where agent wants to explicitly read)
        # but it's usually not needed since profile is in context
        if "get_user_profile" not in existing_tool_names:
            try:
                from .user_profile_tools import get_user_profile
                tools.append(get_user_profile)
                logger.debug(f"Automatically added get_user_profile tool to agent {config.get('name', 'unknown')} (optional, profile already in context)")
            except Exception as e:
                logger.warning(f"Failed to automatically add get_user_profile tool: {e}")
        
        return tools
    
    def _create_mcp_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create MCP tools using the specialized MCP tools module."""
        from .mcp_tools import create_mcp_tools_from_config
        return create_mcp_tools_from_config(config)
    
    def _create_google_search_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create Google Search tools using the specialized Google tools module."""
        from .google_tools import create_google_search_tools_from_config
        return create_google_search_tools_from_config(config)
    
    def _create_google_drive_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create Google Drive tools using the specialized Google tools module."""
        from .google_tools import create_google_drive_tools_from_config
        return create_google_drive_tools_from_config(config)
    
    def _create_cv_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create CV tools using the specialized CV tools module."""
        from .cv_tools import create_cv_tools_from_config
        return create_cv_tools_from_config(config)

    def _create_supabase_storage_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create Supabase storage tools from configuration."""
        from .supabase_tools import create_supabase_storage_tools_from_config
        return create_supabase_storage_tools_from_config(config)

    def _create_image_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create image generation tools using the specialized image tools module."""
        from .image_tools import create_image_tools_from_config
        return create_image_tools_from_config(config)

    def _create_custom_function_tools(self, custom_functions: List[str], config: Dict[str, Any]) -> List[Any]:
        """Create custom function tools using the specialized custom tools module."""
        from .custom_tools import create_custom_function_tools
        agent_name = config.get('name', 'unknown')
        return create_custom_function_tools(custom_functions, agent_name)

    def _create_memory_blocks_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create local memory blocks tools (DB-backed, project-scoped)."""
        from .memory_blocks_tools import create_memory_blocks_tools_from_config
        return create_memory_blocks_tools_from_config(config)
    
    def _create_file_search_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create File Search tools using the specialized file search tools module."""
        from .file_search_tools import create_file_search_tools_from_config
        return create_file_search_tools_from_config(config)
    
    def _create_user_profile_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create user profile tools using the specialized user profile tools module."""
        from .user_profile_tools import create_user_profile_tools_from_config
        return create_user_profile_tools_from_config(config)
    
    
    def get_available_tool_types(self) -> List[str]:
        """
        Get list of available tool types.
        
        Returns:
            List of available tool type names
        """
        return list(self._tool_creators.keys())
    
    def register_tool_creator(self, tool_type: str, creator_func) -> None:
        """
        Register a new tool creator function.
        
        Args:
            tool_type: Name of the tool type
            creator_func: Function that creates tools of this type
        """
        self._tool_creators[tool_type] = creator_func
        logger.info(f"Registered tool creator for type: {tool_type}")


# Global instance for easy access
_tool_factory = None

def get_tool_factory() -> ToolFactory:
    """Get the global tool factory instance."""
    global _tool_factory
    if _tool_factory is None:
        _tool_factory = ToolFactory()
    return _tool_factory
