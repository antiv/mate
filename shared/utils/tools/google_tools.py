"""
Google services tools creation and management.
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


# def create_google_search_tool(agent_name: str) -> Any:
#     """
#     Create a web search tool using Tavily via LangChain integration.
    
#     Args:
#         agent_name: Name of the agent (for logging)
        
#     Returns:
#         LangchainTool instance with Tavily search or None if creation fails
#     """
#     try:
#         import os
#         from google.adk.tools.langchain_tool import LangchainTool
#         from langchain_community.tools import TavilySearchResults
        
#         # Check if TAVILY_API_KEY is available
#         if not os.getenv("TAVILY_API_KEY"):
#             logger.warning(f"TAVILY_API_KEY not set for {agent_name}, skipping Tavily search tool")
#             return None
        
#         # Create Tavily search tool instance
#         tavily_tool_instance = TavilySearchResults(
#             max_results=5,
#             search_depth="advanced",
#             include_answer=True,
#             include_raw_content=True,
#             include_images=True,
#         )
        
#         # Wrap with LangchainTool for ADK compatibility
#         adk_tavily_tool = LangchainTool(tool=tavily_tool_instance)
        
#         logger.info(f"Created Tavily web search tool for {agent_name}")
#         return adk_tavily_tool
        
#     except ImportError as e:
#         logger.warning(f"Tavily search tool not available for {agent_name}: {e}")
#         return None
#     except Exception as e:
#         logger.error(f"Failed to create Tavily search tool for {agent_name}: {e}")
#         return None


# def create_google_search_tools_from_config(config: Dict[str, Any]) -> List[Any]:
#     """
#     Create Google Search tools from agent configuration.
    
#     Args:
#         config: Agent configuration dictionary
        
#     Returns:
#         List of Google Search tools
#     """
#     tools = []
#     agent_name = config.get('name', 'unknown')
    
#     search_tool = create_google_search_tool(agent_name)
#     if search_tool:
#         tools.append(search_tool)
    
#     return tools


def create_google_drive_tools(agent_name: str) -> List[Any]:
    """
    Create Google Drive tools for CV processing.
    
    Args:
        agent_name: Name of the agent (for logging)
        
    Returns:
        List of Google Drive tools
    """
    tools = []
    
    try:
        from .google_drive_tools import (
            list_files_in_folder,
            read_google_doc,
            read_google_doc_by_name,
            search_files,
            get_file_metadata,
            get_file_sharing_permissions,
            find_by_name
        )
        # from .cv_analyzer_tools import (
        #     read_and_analyze_cv,
        #     read_and_analyze_cv_by_name,
        #     analyze_cv_content,
        #     analyze_cv_by_name
        # )
        
        tools.extend([
            list_files_in_folder,
            read_google_doc,
            read_google_doc_by_name,
            search_files,
            get_file_metadata,
            get_file_sharing_permissions,
            find_by_name,
            # read_and_analyze_cv,
            # read_and_analyze_cv_by_name,
            # analyze_cv_content,
            # analyze_cv_by_name
        ])
        
        logger.info(f"Created {len(tools)} Google Drive tools for {agent_name}")
        
    except ImportError as e:
        logger.warning(f"Google Drive tools not available for {agent_name}: {e}")
    except Exception as e:
        logger.error(f"Failed to create Google Drive tools for {agent_name}: {e}")
    
    return tools


def create_google_drive_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create Google Drive tools from agent configuration.
    
    Args:
        config: Agent configuration dictionary
        
    Returns:
        List of Google Drive tools
    """
    agent_name = config.get('name', 'unknown')
    return create_google_drive_tools(agent_name)
