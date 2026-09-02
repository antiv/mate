"""
MCP (Model Context Protocol) tools creation and management.
"""

import logging
import json
import os
import re
import shutil
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

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
        McpToolset instance or None if creation fails
    """
    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams, StdioServerParameters

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

        mcp_toolset = McpToolset(
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


def create_mcp_toolset_http(
    url: str,
    headers: Dict[str, str],
    agent_name: str = "unknown",
    timeout: int = 60,
    connect_timeout: float = 5.0,
    transport: str = "streamable_http",
) -> Any:
    """
    Create an MCP toolset that talks to a remote server over HTTP.

    MATE already serves its own agents as HTTP MCP servers, but the client could
    only speak stdio, so remote servers had to be reached by spawning
    `npx mcp-remote` as a subprocess. This removes that hop, and the Node
    dependency along with it.

    Args:
        url: Server endpoint, http or https.
        headers: Extra request headers, typically Authorization.
        agent_name: Name of the agent (for logging).
        timeout: How long a tool call may take before giving up, in seconds.
            Matches the meaning `timeout` already has for stdio servers.
        connect_timeout: How long to wait for the connection itself.
        transport: 'streamable_http' (default) or 'sse' for older servers.

    Returns:
        McpToolset instance or None if creation fails
    """
    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import (
            SseConnectionParams,
            StreamableHTTPConnectionParams,
        )

        # A stdio server is a local process; an HTTP one is wherever the URL points.
        # Refuse anything that is not http(s) rather than handing an arbitrary
        # scheme to the transport.
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            logger.error(
                f"Skipping MCP server for agent '{agent_name}': "
                f"url '{url}' is not http or https. "
                f"The agent will respond without this MCP tool."
            )
            return None

        params_cls = SseConnectionParams if transport == "sse" else StreamableHTTPConnectionParams
        mcp_toolset = McpToolset(
            connection_params=params_cls(
                url=url,
                headers=headers or None,
                timeout=float(connect_timeout),
                sse_read_timeout=float(timeout),
            ),
        )

        logger.info(
            f"Created {transport} MCP toolset for {agent_name} at {url}"
        )
        return mcp_toolset

    except ImportError as e:
        logger.warning(f"MCP tools not available for agent {agent_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to create HTTP MCP toolset for agent {agent_name}: {e}")
        return None


# Secrets do not belong in agent config rows, so string values in an MCP server
# entry may reference the server environment as ${VAR}.
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(obj: Any, server_name: str, agent_name: str) -> Optional[Any]:
    """
    Replace ${VAR} references with values from the environment, recursing through
    lists and dicts.

    Returns None if any referenced variable is unset, having logged which ones.
    Skipping the server is the safe failure: substituting an empty string would
    send an unauthenticated request, and leaving the placeholder would send the
    literal text `${VAR}` as the credential.
    """
    missing = set()

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            def substitute(match):
                name = match.group(1)
                resolved = os.environ.get(name)
                if resolved is None:
                    missing.add(name)
                    return match.group(0)
                return resolved
            return _ENV_PLACEHOLDER.sub(substitute, value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    resolved = walk(obj)
    if missing:
        logger.error(
            f"Skipping MCP server '{server_name}' for agent '{agent_name}': "
            f"{', '.join(sorted(missing))} not set in the environment. "
            f"The agent will respond without this MCP tool."
        )
        return None
    return resolved


def _parse_json_field(value: Any, field: str, server_name: str, agent_name: str) -> Optional[Any]:
    """
    Config fields arrive either already parsed or as a JSON string, depending on
    whether they came from the database or the dashboard. Returns None when the
    string is not valid JSON, having logged which field and which server.
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.error(
            f"Invalid JSON in {field} for server '{server_name}' in agent '{agent_name}': {value}"
        )
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
                    timeout = server_config.get('timeout', 60)
                    url = server_config.get('url')

                    if url:
                        # A url means a remote server we can reach directly, with no
                        # subprocess in between.
                        headers = _parse_json_field(
                            server_config.get('headers', {}), 'headers', server_name, agent_name
                        ) or {}
                        # Configs in the wild spell the transport as either key.
                        transport = (
                            server_config.get('transport')
                            or server_config.get('type')
                            or 'streamable_http'
                        )
                        if server_config.get('command'):
                            logger.warning(
                                f"MCP server '{server_name}' in agent '{agent_name}' sets both "
                                f"url and command; using url and ignoring command"
                            )
                        # Interpolate after parsing, so a secret containing a quote
                        # cannot corrupt a field that arrived as a JSON string.
                        resolved = _resolve_env(
                            {'url': url, 'headers': headers}, server_name, agent_name)
                        if resolved is None:
                            continue
                        mcp_toolset = create_mcp_toolset_http(
                            resolved['url'],
                            resolved['headers'],
                            f"{agent_name}_{server_name}",
                            timeout=timeout,
                            connect_timeout=server_config.get('connect_timeout', 5.0),
                            transport=transport,
                        )
                    else:
                        command = server_config.get('command')
                        args = _parse_json_field(
                            server_config.get('args', []), 'args', server_name, agent_name
                        )
                        if args is None:
                            continue
                        env = _parse_json_field(
                            server_config.get('env', {}), 'env', server_name, agent_name
                        ) or {}

                        if not (command and args):
                            logger.warning(
                                f"Invalid MCP server configuration for '{server_name}' in agent "
                                f"'{agent_name}': needs either a url, or a command and args"
                            )
                            continue

                        resolved = _resolve_env(
                            {'command': command, 'args': args, 'env': env},
                            server_name, agent_name)
                        if resolved is None:
                            continue
                        mcp_toolset = create_mcp_toolset_command(
                            resolved['command'], resolved['args'], resolved['env'],
                            f"{agent_name}_{server_name}", timeout=timeout
                        )

                    if mcp_toolset:
                        tools.append(mcp_toolset)
                        logger.info(f"Created MCP toolset for server '{server_name}' in agent '{agent_name}'")
                    else:
                        logger.warning(f"Failed to create MCP toolset for server '{server_name}' in agent '{agent_name}'")

                except Exception as e:
                    logger.error(f"Failed to create MCP toolset for server '{server_name}' in agent '{agent_name}': {e}")
                    
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse mcp_servers_config JSON for agent {agent_name}: {e}")
        except Exception as e:
            logger.error(f"Failed to process mcp_servers_config for agent {agent_name}: {e}")
    
    return tools
