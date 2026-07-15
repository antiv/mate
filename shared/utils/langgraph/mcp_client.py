"""
MCP tools for the LangGraph runtime.

Reads the same mcp_servers_config JSON ({"mcpServers": {name: {command, args,
env, timeout}}}) that shared/utils/tools/mcp_tools.py uses for ADK, but builds
LangChain tools via langchain-mcp-adapters (stdio transport).
"""

import json
import logging
import os
from typing import Any, Dict, List

from shared.utils.tools.mcp_tools import resolve_mcp_command

logger = logging.getLogger(__name__)


def _parse_json_maybe(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value if value is not None else fallback


async def create_mcp_tools(mcp_servers_config: Any, agent_name: str = "unknown") -> List[Any]:
    """Build LangChain tools for every configured stdio MCP server."""
    if not mcp_servers_config:
        return []
    servers_config = _parse_json_maybe(mcp_servers_config, {})
    mcp_servers = (servers_config or {}).get("mcpServers", {})
    if not mcp_servers:
        return []

    connections: Dict[str, Dict[str, Any]] = {}
    for server_name, server_config in mcp_servers.items():
        command = server_config.get("command")
        args = _parse_json_maybe(server_config.get("args", []), [])
        env = _parse_json_maybe(server_config.get("env", {}), {})
        if not command or not args:
            logger.warning(f"Invalid MCP server configuration for '{server_name}' in agent '{agent_name}': missing command or args")
            continue
        resolved_command = resolve_mcp_command(command)
        if resolved_command is None:
            logger.error(f"Skipping MCP server '{server_name}' for agent '{agent_name}': command '{command}' not found")
            continue
        env_vars = os.environ.copy()
        env_vars.update(env or {})
        connections[server_name] = {
            "transport": "stdio",
            "command": resolved_command,
            "args": args,
            "env": env_vars,
        }

    if not connections:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        client = MultiServerMCPClient(connections)
        tools = await client.get_tools()
        logger.info(f"Created {len(tools)} MCP tools from {len(connections)} server(s) for agent '{agent_name}'")
        return tools
    except Exception as e:
        logger.error(f"Failed to create MCP tools for agent '{agent_name}': {e}")
        return []
