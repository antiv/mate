"""
You.com search tools creation and management.
"""

import logging
import os
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


def create_youcom_search_tool(agent_name: str) -> Any:
    """
    Create a You.com web search tool using MCP integration via LangChain.
    
    Args:
        agent_name: Name of the agent (for logging)
        
    Returns:
        LangchainTool instance with You.com search or None if creation fails
    """
    try:
        from google.adk.tools.langchain_tool import LangchainTool
        from langchain_core.tools import tool
        import requests
        import json
        
        # Check if YDC_API_KEY is available (optional for keyless access)
        api_key = os.getenv("YDC_API_KEY")
        
        @tool
        def youcom_search(query: str, count: int = 10) -> str:
            """
            Search the web using You.com for current information.
            
            Args:
                query: The search query to execute
                count: Number of results to return (default: 10)
            
            Returns:
                Search results as formatted text with titles, snippets, and URLs
            """
            try:
                # You.com MCP API endpoint
                url = "https://api.you.com/mcp"
                
                # Prepare headers
                headers = {
                    "Content-Type": "application/json"
                }
                
                # Add API key if available
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                
                # Prepare MCP request payload for you-search tool
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "you-search",
                        "arguments": {
                            "query": query,
                            "count": count
                        }
                    }
                }
                
                # Make request with timeout
                response = requests.post(url, json=payload, headers=headers, timeout=30)
                
                if response.status_code == 402:
                    # Handle payment required - fallback to keyless message
                    return f"You.com search requires authentication or payment. Please set YDC_API_KEY environment variable or use a keyless payment method. Query: {query}"
                
                response.raise_for_status()
                
                data = response.json()
                
                if "error" in data:
                    logger.error(f"You.com API error for {agent_name}: {data['error']}")
                    return f"Search error: {data['error'].get('message', 'Unknown error')}"
                
                # Extract results from MCP response
                result = data.get("result", {})
                
                if isinstance(result, dict) and "results" in result:
                    results = result["results"]
                    
                    # Format results for agent consumption
                    formatted_results = []
                    for idx, item in enumerate(results[:count], 1):
                        title = item.get("title", "No title")
                        snippet = item.get("snippet", item.get("description", "No description"))
                        url = item.get("url", "")
                        
                        formatted_results.append(f"{idx}. {title}\n   {snippet}\n   URL: {url}\n")
                    
                    if formatted_results:
                        return "\n".join(formatted_results)
                    else:
                        return f"No results found for: {query}"
                else:
                    return f"Unexpected response format from You.com search for: {query}"
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error in You.com search for {agent_name}: {e}")
                return f"Network error during search: {str(e)}"
            except Exception as e:
                logger.error(f"Unexpected error in You.com search for {agent_name}: {e}")
                return f"Search error: {str(e)}"
        
        # Wrap with LangchainTool for ADK compatibility
        adk_youcom_tool = LangchainTool(tool=youcom_search)
        
        logger.info(f"Created You.com web search tool for {agent_name}")
        return adk_youcom_tool
        
    except ImportError as e:
        logger.warning(f"You.com search tool not available for {agent_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to create You.com search tool for {agent_name}: {e}")
        return None


def create_youcom_search_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create You.com search tools from agent configuration.
    
    Args:
        config: Agent configuration dictionary
        
    Returns:
        List of You.com search tools
    """
    tools = []
    agent_name = config.get('name', 'unknown')
    
    search_tool = create_youcom_search_tool(agent_name)
    if search_tool:
        tools.append(search_tool)
    
    return tools