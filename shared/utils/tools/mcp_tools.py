"""
MCP (Model Context Protocol) tools creation and management.
"""

import logging
import json
import os
import shutil
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Human-readable install hints for common MCP server commands
_MCP_COMMAND_INSTALL_HINTS: Dict[str, str] = {
    "npx": "Install Node.js from https://nodejs.org/ (includes npx)",
    "node": "Install Node.js from https://nodejs.org/",
    "uvx": "Install uv from https://github.com/astral-sh/uv (includes uvx)",
    "uv": "Install uv from https://github.com/astral-sh/uv",
    "python": "Ensure Python is installed and in PATH",
    "python3": "Ensure Python 3 is installed and in PATH",
    "bun": "Install Bun from https://bun.sh/",
    "deno": "Install Deno from https://deno.land/",
}


def resolve_mcp_command(command: str) -> Optional[str]:
    """
    Resolve the full path of an MCP server command using shutil.which().

    Returns the resolved absolute path if found, or None if not found.
    Logging a warning with an install hint when the command cannot be resolved.
    """
    if os.path.isabs(command) and os.path.isfile(command):
        return command  # Already an absolute path — use as-is

    resolved = shutil.which(command)
    if resolved:
        logger.debug(f"Resolved MCP command '{command}' -> '{resolved}'")
        return resolved

    # Not found — emit a clear, actionable warning
    hint = _MCP_COMMAND_INSTALL_HINTS.get(command, f"Ensure '{command}' is installed and in your PATH")
    logger.warning(
        f"MCP command '{command}' not found in PATH. "
        f"This MCP server will fail to start. "
        f"Fix: {hint}"
    )
    return None


def create_mcp_toolset_command(
    command: str,
    args: List[str],
    env: Dict[str, str],
    agent_name: str = "unknown",
    timeout: int = 60,
) -> Any:
    """
    Create an MCP toolset with command-based server configuration (like Supabase).

    Args:
        command: Command to execute (e.g., 'npx')
        args: List of command arguments
        env: Environment variables as dictionary
        agent_name: Name of the agent (for logging)
        timeout: Timeout in seconds for MCP requests (default 60). Use higher values
            for slow tools like tavily_research (e.g. 300).

    Returns:
        MCPToolset instance or None if creation fails
    """
    try:
        from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams, StdioServerParameters

        # Resolve the command to a full absolute path before spawning.
        # This avoids a cryptic FileNotFoundError at subprocess-creation time and
        # provides a clear, actionable error message when the command is missing.
        resolved_command = resolve_mcp_command(command)
        if resolved_command is None:
            logger.error(
                f"Skipping MCP server for agent '{agent_name}': "
                f"command '{command}' not found. "
                f"The agent will respond without this MCP tool."
            )
            return None

        # Set environment variables
        env_vars = os.environ.copy()
        env_vars.update(env)

        mcp_toolset = MCPToolset(
            connection_params=StdioConnectionParams(
                timeout=float(timeout),
                server_params=StdioServerParameters(
                    command=resolved_command,
                    args=args,
                    env=env_vars,
                ),
            ),
        )

        logger.info(
            f"Created command-based MCP toolset for {agent_name} "
            f"with command: {resolved_command} {' '.join(str(a) for a in args)}"
        )
        return mcp_toolset

    except ImportError as e:
        logger.warning(f"MCP tools not available for agent {agent_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to create command-based MCP toolset for agent {agent_name}: {e}")
        return None


def create_mcp_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create MCP tools from agent configuration using both new multi-server format and legacy format.
    
    Args:
        config: Agent configuration dictionary
        
    Returns:
        List of MCP tools
    """
    tools = []
    agent_name = config.get('name', 'unknown')
    
    # Check for new multi-server MCP configuration
    mcp_servers_config = config.get('mcp_servers_config')
    if mcp_servers_config:
        try:
            if isinstance(mcp_servers_config, str):
                servers_config = json.loads(mcp_servers_config)
            else:
                servers_config = mcp_servers_config
            
            # Extract mcpServers from the configuration
            mcp_servers = servers_config.get('mcpServers', {})
            
            for server_name, server_config in mcp_servers.items():
                try:
                    command = server_config.get('command')
                    args_raw = server_config.get('args', [])
                    env_raw = server_config.get('env', {})
                    
                    # Parse args if it's a JSON string
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON in args for server '{server_name}' in agent '{agent_name}': {args_raw}")
                            continue
                    else:
                        args = args_raw
                    
                    # Parse env if it's a JSON string
                    if isinstance(env_raw, str):
                        try:
                            env = json.loads(env_raw)
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON in env for server '{server_name}' in agent '{agent_name}': {env_raw}")
                            env = {}
                    else:
                        env = env_raw
                    
                    if command and args:
                        timeout = server_config.get('timeout', 60)
                        mcp_toolset = create_mcp_toolset_command(
                            command, args, env, f"{agent_name}_{server_name}", timeout=timeout
                        )
                        if mcp_toolset:
                            tools.append(mcp_toolset)
                            logger.info(f"Created MCP toolset for server '{server_name}' in agent '{agent_name}'")
                        else:
                            logger.warning(f"Failed to create MCP toolset for server '{server_name}' in agent '{agent_name}'")
                    else:
                        logger.warning(f"Invalid MCP server configuration for '{server_name}' in agent '{agent_name}': missing command or args")
                        
                except Exception as e:
                    logger.error(f"Failed to create MCP toolset for server '{server_name}' in agent '{agent_name}': {e}")
                    
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse mcp_servers_config JSON for agent {agent_name}: {e}")
        except Exception as e:
            logger.error(f"Failed to process mcp_servers_config for agent {agent_name}: {e}")
    
    return tools
