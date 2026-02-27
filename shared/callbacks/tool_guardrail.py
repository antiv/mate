from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from typing import Optional, Dict, Any
import json


def block_paris_tool_guardrail(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict]:
    """
    Checks if 'get_weather_stateful' is called for 'Paris'.
    If so, blocks the tool execution and returns a specific error dictionary.
    Otherwise, allows the tool call to proceed by returning None.
    Also validates tool arguments to prevent JSON parsing errors.
    """
    tool_name = tool.name
    agent_name = tool_context.agent_name # Agent attempting the tool call
    print(f"--- Callback: block_paris_tool_guardrail running for tool '{tool_name}' in agent '{agent_name}' ---")
    print(f"--- Callback: Inspecting args: {args} ---")
    
    # Only validate JSON for arguments that are explicitly expected to be JSON
    # Most tools use simple string arguments, not JSON
    try:
        for key, value in args.items():
            if isinstance(value, str) and key.lower() in ['json', 'data', 'payload', 'config']:
                # Only validate JSON for arguments that are explicitly JSON
                if value.strip().startswith('{') or value.strip().startswith('['):
                    try:
                        json.loads(value)
                    except json.JSONDecodeError as e:
                        print(f"--- Callback: Detected malformed JSON in argument '{key}': {value} ---")
                        print(f"--- Callback: JSON Error: {e} ---")
                        return {
                            "status": "error",
                            "error_message": f"Invalid JSON in tool argument '{key}': {str(e)}"
                        }
    except Exception as e:
        print(f"--- Callback: Error validating tool arguments: {e} ---")
        return {
            "status": "error", 
            "error_message": f"Tool argument validation failed: {str(e)}"
        }

    # --- Guardrail Logic ---
    target_tool_name = "get_weather_stateful" # Match the function name used by FunctionTool
    blocked_city = "paris"

    # Check if it's the correct tool and the city argument matches the blocked city
    if tool_name == target_tool_name:
        city_argument = args.get("city", "") # Safely get the 'city' argument
        if city_argument and city_argument.lower() == blocked_city:
            print(f"--- Callback: Detected blocked city '{city_argument}'. Blocking tool execution! ---")
            # Optionally update state
            tool_context.state["guardrail_tool_block_triggered"] = True
            print(f"--- Callback: Set state 'guardrail_tool_block_triggered': True ---")

            # Return a dictionary matching the tool's expected output format for errors
            # This dictionary becomes the tool's result, skipping the actual tool run.
            return {
                "status": "error",
                "error_message": f"Policy restriction: Weather checks for '{city_argument.capitalize()}' are currently disabled by a tool guardrail."
            }
        else:
             print(f"--- Callback: City '{city_argument}' is allowed for tool '{tool_name}'. ---")
    else:
        print(f"--- Callback: Tool '{tool_name}' is not the target tool. Allowing. ---")


    # If the checks above didn't return a dictionary, allow the tool to execute
    print(f"--- Callback: Allowing tool '{tool_name}' to proceed. ---")
    return None # Returning None allows the actual tool function to run
