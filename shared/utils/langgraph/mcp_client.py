"""
MCP tools for the LangGraph runtime.

Reads the same mcp_servers_config JSON ({"mcpServers": {name: {...}}}) that
shared/utils/tools/mcp_tools.py uses for ADK, and builds LangChain tools via
langchain-mcp-adapters:

- an entry with a `url` is a remote server, reached over streamable HTTP, or SSE
  when `transport` (or `type`) is "sse", with optional `headers`, `timeout` and
  `connect_timeout`, as on ADK;
- an entry with `command` and `args` is a local server started over stdio.

${VAR} in either kind is read from the environment, and an unset one skips the
server.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from shared.utils.tools.mcp_tools import _parse_json_field, _resolve_env, resolve_mcp_command

logger = logging.getLogger(__name__)


def _parse_json_maybe(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value if value is not None else fallback


def _http_connection(server_name: str, server_config: Dict[str, Any],
                     agent_name: str) -> Optional[Dict[str, Any]]:
    """The connection for a remote server, or None to skip it (the reason is logged).

    Follows create_mcp_tools_from_config for ADK: ${VAR} in the url and headers is
    resolved after parsing and an unset one skips the server, the scheme must be
    http or https, `timeout` bounds a tool call and `connect_timeout` the connection.
    """
    headers = _parse_json_field(server_config.get("headers", {}), "headers",
                                server_name, agent_name) or {}
    transport = server_config.get("transport") or server_config.get("type") or "streamable_http"
    if server_config.get("command"):
        logger.warning(f"MCP server '{server_name}' in agent '{agent_name}' sets both "
                       f"url and command; using url and ignoring command")
    resolved = _resolve_env({"url": server_config["url"], "headers": headers},
                            server_name, agent_name)
    if resolved is None:
        return None
    url = resolved["url"]
    if urlparse(url).scheme.lower() not in ("http", "https"):
        logger.error(f"Skipping MCP server '{server_name}' for agent '{agent_name}': "
                     f"url '{url}' is not http or https. "
                     f"The agent will respond without this MCP tool.")
        return None
    return {
        "transport": "sse" if transport == "sse" else "streamable_http",
        "url": url,
        "headers": resolved["headers"] or None,
        "timeout": float(server_config.get("connect_timeout", 5.0)),
        "sse_read_timeout": float(server_config.get("timeout", 60)),
    }


def _stdio_connection(server_name: str, server_config: Dict[str, Any],
                      agent_name: str) -> Optional[Dict[str, Any]]:
    command = server_config.get("command")
    args = _parse_json_maybe(server_config.get("args", []), [])
    env = _parse_json_maybe(server_config.get("env", {}), {})
    if not command or not args:
        logger.warning(f"Invalid MCP server configuration for '{server_name}' in agent "
                       f"'{agent_name}': needs either a url, or a command and args")
        return None
    # ${VAR} as on ADK: an unset one skips the server rather than passing the
    # placeholder on as the value
    resolved = _resolve_env({"command": command, "args": args, "env": env or {}},
                            server_name, agent_name)
    if resolved is None:
        return None
    resolved_command = resolve_mcp_command(resolved["command"])
    if resolved_command is None:
        logger.error(f"Skipping MCP server '{server_name}' for agent '{agent_name}': command '{command}' not found")
        return None
    env_vars = os.environ.copy()
    env_vars.update(resolved["env"])
    return {
        "transport": "stdio",
        "command": resolved_command,
        "args": resolved["args"],
        "env": env_vars,
    }


async def create_mcp_tools(mcp_servers_config: Any, agent_name: str = "unknown") -> List[Any]:
    """Build LangChain tools for every configured MCP server, remote or stdio."""
    if not mcp_servers_config:
        return []
    servers_config = _parse_json_maybe(mcp_servers_config, {})
    mcp_servers = (servers_config or {}).get("mcpServers", {})
    if not mcp_servers:
        return []

    connections: Dict[str, Dict[str, Any]] = {}
    for server_name, server_config in mcp_servers.items():
        if server_config.get("url"):
            connection = _http_connection(server_name, server_config, agent_name)
        else:
            connection = _stdio_connection(server_name, server_config, agent_name)
        if connection is not None:
            connections[server_name] = connection

    if not connections:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        client = MultiServerMCPClient(connections)
    except Exception as e:
        logger.error(f"Failed to create MCP tools for agent '{agent_name}': {e}")
        return []

    # One server at a time, so a remote server that is down costs only its own
    # tools, as on ADK where each server is its own toolset
    tools: List[Any] = []
    for server_name in connections:
        try:
            tools.extend(await client.get_tools(server_name=server_name))
        except Exception as e:
            logger.error(f"Failed to load tools from MCP server '{server_name}' for agent "
                         f"'{agent_name}': {e}. The agent will respond without them.")
    logger.info(f"Created {len(tools)} MCP tools from {len(connections)} server(s) for agent '{agent_name}'")
    return tools
