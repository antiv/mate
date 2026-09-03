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


async def google_search(query: str) -> str:
    """
    Search the web for real-time information, websites, facts, and prices.
    
    Args:
        query: The search query string.
        
    Returns:
        Structured search results with titles, URLs, and summaries.
    """
    import os
    tavily_key = os.getenv("TAVILY_API_KEY")
    if tavily_key:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=tavily_key)
            res = client.search(query, max_results=5)
            formatted = []
            for r in res.get("results", []):
                formatted.append(f"Title: {r.get('title')}\nURL: {r.get('url')}\nSnippet: {r.get('content')}")
            if formatted:
                return "\n\n---\n\n".join(formatted)
        except Exception as te:
            logger.warning(f"Tavily search error: {te}, falling back to web search")

    try:
        import httpx
        import urllib.parse
        import re
        from html import unescape

        encoded = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,sr;q=0.8,sl;q=0.7",
        }
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                matches = re.findall(
                    r'<a class="result__snippet"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    resp.text,
                    re.DOTALL
                )
                if matches:
                    results = []
                    for raw_url, raw_snip in matches[:5]:
                        clean_snip = unescape(re.sub(r'<[^>]+>', '', raw_snip).strip())
                        actual_url = raw_url
                        if "uddg=" in actual_url:
                            actual_url = urllib.parse.unquote(actual_url.split("uddg=")[-1].split("&")[0])
                        actual_url = urllib.parse.unquote(actual_url)
                        results.append(f"URL: {actual_url}\nSnippet: {clean_snip}")
                    return "\n\n---\n\n".join(results)
    except Exception as e:
        logger.error(f"Web search error for query '{query}': {e}")
        return f"Could not perform web search: {e}"

    return f"No search results found for query: {query}"


def create_google_search_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create Google Search tools from agent configuration.
    Returns the universal async google_search tool (works with Gemini, OpenRouter, DeepSeek, Ollama, etc.).
    """
    return [google_search]


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
