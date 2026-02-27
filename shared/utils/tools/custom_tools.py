"""
Custom tools creation and management.
"""

import logging
import importlib
from typing import List, Any, Optional

logger = logging.getLogger(__name__)


def create_custom_function_tools(custom_functions: List[str], agent_name: str = "unknown") -> List[Any]:
    """
    Create custom function tools based on function names.
    
    Args:
        custom_functions: List of custom function names to create
        agent_name: Name of the agent (for logging)
        
    Returns:
        List of custom function tools
    """
    tools = []
    
    for func_name in custom_functions:
        try:
            tool = load_custom_function(func_name)
            if tool:
                tools.append(tool)
                logger.info(f"Created custom function tool '{func_name}' for {agent_name}")
            else:
                logger.warning(f"Custom function tool '{func_name}' not found for {agent_name}")
        except Exception as e:
            logger.error(f"Failed to create custom function tool '{func_name}' for {agent_name}: {e}")
    
    return tools


def load_custom_function(func_name: str) -> Optional[Any]:
    """
    Load a custom function by name.
    
    This function attempts to dynamically import custom functions from
    various locations. You can extend this to support your custom function
    organization.
    
    Args:
        func_name: Name of the custom function to load
        
    Returns:
        Custom function tool or None if not found
    """
    # List of possible locations for custom functions
    search_paths = [
        f'custom_tools.{func_name}',
        f'tools.{func_name}',
        f'utils.custom_tools.{func_name}',
        f'..custom_tools.{func_name}',
    ]
    
    for module_path in search_paths:
        try:
            module = importlib.import_module(module_path)
            # Try to get the function with the same name as the module
            if hasattr(module, func_name):
                return getattr(module, func_name)
            # Try to get a default function name
            elif hasattr(module, 'main'):
                return getattr(module, 'main')
            elif hasattr(module, 'tool'):
                return getattr(module, 'tool')
        except ImportError:
            continue
        except AttributeError:
            continue
    
    logger.warning(f"Custom function '{func_name}' not found in any search path")
    return None


def register_custom_tool_path(path: str) -> None:
    """
    Register a new search path for custom tools.
    
    Args:
        path: Module path to search for custom tools
    """
    # This could be extended to maintain a registry of custom tool paths
    logger.info(f"Custom tool path registration requested: {path}")
    # Implementation would depend on how you want to manage custom tool paths


# Example custom tool implementations
def example_custom_tool(input_data: str) -> str:
    """
    Example custom tool implementation.
    
    Args:
        input_data: Input data for the tool
        
    Returns:
        Processed output
    """
    return f"Processed: {input_data}"


# Registry of built-in custom tools
BUILTIN_CUSTOM_TOOLS = {
    'example_tool': example_custom_tool,
    'create_agent': None,  # Will be imported dynamically from create_agent_tool
}


def get_builtin_custom_tool(tool_name: str) -> Optional[Any]:
    """
    Get a built-in custom tool by name.
    
    Args:
        tool_name: Name of the built-in custom tool
        
    Returns:
        Custom tool function or None if not found
    """
    tool = BUILTIN_CUSTOM_TOOLS.get(tool_name)
    
    # Lazy import for create_agent tool
    if tool_name == 'create_agent' and tool is None:
        try:
            from .create_agent_tool import create_agent
            return create_agent
        except ImportError:
            return None
    
    return tool
