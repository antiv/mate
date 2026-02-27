from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from typing import Optional, Dict, Any
import json
import re


def mcp_tool_validation_guardrail(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict]:
    """
    Validates MCP tool calls to prevent JSON parsing errors and other common issues.
    This guardrail specifically targets MCP tools that might return malformed JSON.
    """
    tool_name = tool.name
    agent_name = tool_context.agent_name
    
    print(f"--- MCP Guardrail: Validating tool '{tool_name}' in agent '{agent_name}' ---")
    print(f"--- MCP Guardrail: Tool args: {args} ---")
    
    # Check if this is an MCP tool
    is_mcp_tool = 'mcp' in tool_name.lower()
    
    if not is_mcp_tool:
        print(f"--- MCP Guardrail: Tool '{tool_name}' is not an MCP tool, skipping validation ---")
        return None
    
    # For MCP tools, we only validate for obvious issues, not JSON parsing
    # MCP tools use regular string/primitive arguments, not JSON
    try:
        # Only check for completely empty required parameters
        if is_mcp_tool and 'query' in args and isinstance(args['query'], str):
            if len(args['query'].strip()) == 0:
                print(f"--- MCP Guardrail: Empty query parameter detected ---")
                return {
                    "status": "error",
                    "error_message": f"MCP tool '{tool_name}' requires a non-empty query parameter"
                }

        # Only validate JSON if the argument is explicitly expected to be JSON
        # Most MCP tools use simple string arguments
        for key, value in args.items():
            if isinstance(value, str) and key.lower() in ['json', 'data', 'payload']:
                # Only validate JSON for arguments that are explicitly JSON
                if _looks_like_json(value):
                    try:
                        json.loads(value)
                    except json.JSONDecodeError as e:
                        print(f"--- MCP Guardrail: JSON parse error in '{key}': {e} ---")
                        return {
                            "status": "error",
                            "error_message": f"MCP tool '{tool_name}' received invalid JSON in argument '{key}': {str(e)}"
                        }
        
        print(f"--- MCP Guardrail: Tool '{tool_name}' validation passed ---")
        return None
        
    except Exception as e:
        print(f"--- MCP Guardrail: Unexpected error during validation: {e} ---")
        return {
            "status": "error",
            "error_message": f"MCP tool validation failed: {str(e)}"
        }


def _has_malformed_json_patterns(value: str) -> bool:
    """Check for common malformed JSON patterns."""
    # Check for trailing commas
    if re.search(r',\s*[}\]])', value):
        return True
    
    # Check for unescaped quotes in strings
    if re.search(r'"[^"]*"[^"]*"[^"]*"', value):
        return True
    
    # Check for missing quotes around keys
    if re.search(r'{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*:', value):
        return True
    
    return False


def _looks_like_json(value: str) -> bool:
    """Check if a string looks like it should be JSON."""
    stripped = value.strip()
    return (stripped.startswith('{') and stripped.endswith('}')) or \
           (stripped.startswith('[') and stripped.endswith(']'))
