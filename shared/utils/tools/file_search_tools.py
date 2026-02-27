"""
Gemini File Search tools for RAG functionality.
"""
import os
import logging
import time
from typing import Dict, List, Any, Optional
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

# Try to import Google Generative AI
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None
    logger.warning("Google Generative AI package not available. File Search tools will not work.")


def get_file_search_client():
    """Get authenticated Gemini File Search client."""
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        raise Exception("GOOGLE_API_KEY not configured")
    if genai is None:
        raise Exception("Google Generative AI package not installed. Install with: pip install google-genai")
    return genai.Client(api_key=api_key)


def create_file_search_store(
    display_name: str,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Create a File Search store.
    
    Args:
        display_name: Human-readable name for the store
        tool_context: Optional tool context
    
    Returns:
        Dict with success status and store info
    """
    try:
        client = get_file_search_client()
        
        file_search_store = client.file_search_stores.create(
            config={'display_name': display_name}
        )
        
        return {
            "success": True,
            "store_name": file_search_store.name,
            "display_name": display_name
        }
    except Exception as e:
        logger.error(f"Failed to create file search store: {e}")
        return {"success": False, "error": str(e)}


def upload_file_to_store(
    file_path: str,
    store_name: str,
    display_name: Optional[str] = None,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Upload a file to a File Search store.
    
    Args:
        file_path: Path to the file to upload
        store_name: Full store name from Gemini API (e.g., "fileSearchStores/abc123")
        display_name: Optional display name for the file
        tool_context: Optional tool context
    
    Returns:
        Dict with success status and document info
    """
    try:
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}
        
        client = get_file_search_client()
        
        operation = client.file_search_stores.upload_to_file_search_store(
            file=file_path,
            file_search_store_name=store_name,
            config={
                'display_name': display_name or os.path.basename(file_path),
            }
        )
        
        # Wait for upload to complete
        max_wait_time = 300  # 5 minutes max
        wait_time = 0
        while not operation.done and wait_time < max_wait_time:
            time.sleep(2)
            wait_time += 2
            operation = client.operations.get(operation)
        
        if not operation.done:
            return {
                "success": False,
                "error": "Upload operation timed out",
                "operation": str(operation)
            }
        
        # Extract document name from response
        document_name = None
        if hasattr(operation, 'response') and operation.response:
            response = operation.response
            # Try multiple ways to get the document name
            if hasattr(response, 'name') and response.name:
                document_name = response.name
            elif hasattr(response, 'document'):
                doc = response.document
                if hasattr(doc, 'name') and doc.name:
                    document_name = doc.name
            # Try accessing as dict if it's a dict-like object
            elif isinstance(response, dict):
                document_name = response.get('name') or response.get('document', {}).get('name')
            
            # Log the response structure for debugging
            logger.debug(f"Operation response type: {type(response)}, attributes: {dir(response) if hasattr(response, '__dict__') else 'N/A'}")
        
        # If we still don't have a document name, generate one from the file path
        # Format: fileSearchDocuments/{hash} or use display_name as fallback
        if not document_name:
            import hashlib
            file_hash = hashlib.md5(file_path.encode()).hexdigest()[:16]
            document_name = f"fileSearchDocuments/{file_hash}"
            logger.warning(f"Could not extract document_name from API response, using generated: {document_name}")
            logger.debug(f"Operation response: {str(operation.response) if hasattr(operation, 'response') else 'No response'}")
        
        return {
            "success": True,
            "document_name": document_name,
            "operation": str(operation.response) if hasattr(operation, 'response') else None
        }
    except Exception as e:
        logger.error(f"Failed to upload file: {e}")
        return {"success": False, "error": str(e)}


def query_file_search(
    question: str,
    store_names: List[str] = None,
    model: str = "gemini-2.5-flash",
    tool_context: ToolContext = None
) -> str:
    """
    Query File Search stores using Gemini API with RAG.
    
    This is a custom tool that wraps File Search functionality, allowing it to work
    with ADK agents by being passed via Agent.tools instead of generate_content_config.tools.
    
    Args:
        question: The question to ask about the files in the stores
        store_names: List of File Search store names (e.g., ["fileSearchStores/abc123"])
                    If not provided, will try to get from agent's tool_config
        model: Gemini model to use (default: "gemini-2.5-flash")
        tool_context: Optional tool context
    
    Returns:
        Response from Gemini API with File Search results
    """
    try:
        if genai is None or types is None:
            return "Error: Google Generative AI package not available. Install with: pip install google-genai"
        
        api_key = os.getenv('GOOGLE_API_KEY')
        if not api_key:
            return "Error: GOOGLE_API_KEY not configured"
        
        client = genai.Client(api_key=api_key)
        
        # If store_names not provided, try to get from tool_config
        if not store_names:
            # Try to extract from tool_context if available
            # This is a fallback - ideally store_names should be passed
            logger.warning("No store_names provided to query_file_search, File Search may not work")
            return "Error: No File Search stores configured. Please provide store_names parameter."
        
        # Create File Search tool configuration
        file_search_tool = types.Tool(
            file_search=types.FileSearch(
                file_search_store_names=store_names
            )
        )
        
        # Generate content with File Search
        response = client.models.generate_content(
            model=model,
            contents=question,
            config=types.GenerateContentConfig(
                tools=[file_search_tool]
            )
        )
        
        # Extract text from response
        if hasattr(response, 'text'):
            return response.text
        elif hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                parts = candidate.content.parts
                if parts:
                    return parts[0].text if hasattr(parts[0], 'text') else str(parts[0])
        
        return str(response)
        
    except Exception as e:
        logger.error(f"Failed to query File Search: {e}", exc_info=True)
        return f"Error querying File Search: {str(e)}"


def create_file_search_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create File Search tool integration for agent.
    
    Since ADK doesn't allow tools in generate_content_config.tools, we create
    a custom Python function tool that wraps File Search functionality.
    
    Args:
        config: Agent configuration dictionary containing tool_config with file_search config
    
    Returns:
        List of File Search query tools (one per store or combined)
    """
    tools = []
    
    try:
        # Parse tool_config to get File Search configuration
        tool_config = config.get('tool_config')
        if not tool_config:
            return []
        
        if isinstance(tool_config, str):
            import json
            tool_config_dict = json.loads(tool_config)
        else:
            tool_config_dict = tool_config
        
        file_search_config = tool_config_dict.get('file_search')
        if not file_search_config or not file_search_config.get('enabled'):
            return []
        
        store_names = file_search_config.get('store_names', [])
        # Support backward compatibility with single store_name
        if not store_names and file_search_config.get('store_name'):
            store_names = [file_search_config.get('store_name')]
        
        if not store_names:
            logger.warning("File Search enabled but no store_names provided")
            return []
        
        # Get model from config or use default
        model = file_search_config.get('model', 'gemini-2.5-flash')
        
        # Create a tool function with store_names bound
        # We'll create one tool that can query all stores
        def create_file_search_query_tool(stores: List[str], model_name: str):
            """Create a File Search query tool with stores bound."""
            def file_search_query(question: str) -> str:
                """Query File Search stores with a question."""
                return query_file_search(
                    question=question,
                    store_names=stores,
                    model=model_name
                )
            
            # Set function metadata for ADK
            file_search_query.__name__ = "query_file_search"
            file_search_query.__doc__ = f"""Query File Search stores with RAG.
            
            This tool allows you to ask questions about files stored in File Search stores.
            The tool will search through the following stores: {', '.join(stores)}
            
            Args:
                question: The question to ask about the files
            
            Returns:
                Response from Gemini API with relevant information from the files
            """
            
            return file_search_query
        
        # Create the tool with stores bound
        file_search_tool = create_file_search_query_tool(store_names, model)
        tools.append(file_search_tool)
        
        logger.info(f"✅ Created File Search query tool for agent {config.get('name', 'unknown')} with stores: {store_names}")
        
    except Exception as e:
        logger.error(f"Failed to create File Search tools: {e}", exc_info=True)
    
    return tools

