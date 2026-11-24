"""
MCP tools for the LangGraph runtime.

Reads the same mcp_servers_config JSON ({"mcpServers": {name: {...}}}) that
shared/utils/tools/mcp_tools.py uses for ADK, and builds LangChain tools on the MCP
SDK's client:

- an entry with a `url` is a remote server, reached over streamable HTTP, or SSE
  when `transport` (or `type`) is "sse", with optional `headers`, `timeout` and
  `connect_timeout`, as on ADK;
- an entry with `command` and `args` is a local server started over stdio.

${VAR} in either kind is read from the environment, and an unset one skips the
server.

Each call of a tool opens a session of its own. What the server marks as an error is
given to the model as a failed tool call; a connection that fails raises.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx2
from langchain_core.messages.content import create_file_block, create_image_block, create_text_block
from langchain_core.tools import StructuredTool, ToolException
from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from shared.utils.tools.mcp_tools import _parse_json_field, _resolve_env, resolve_mcp_command

logger = logging.getLogger(__name__)

# The SDK's own limit for a streamable HTTP event is 1 MB, which a tool that returns an
# image passes. The SSE transport's can't be set.
MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_TOOL_PAGES = 1000


def _parse_json_maybe(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value if value is not None else fallback


def _http_connection(server_name: str, server_config: Dict[str, Any],
                     agent_name: str, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
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
    from shared.utils.outbound import outbound_refusal
    refusal = outbound_refusal(url, agent_name=agent_name, project_id=project_id)
    if refusal:
        logger.error(f"Skipping MCP server '{server_name}' for agent '{agent_name}': {refusal}")
        return None
    return {
        "transport": "sse" if transport == "sse" else "streamable_http",
        "url": url,
        "headers": resolved["headers"] or None,
        "timeout": float(server_config.get("connect_timeout", 5.0)),
        "sse_read_timeout": float(server_config.get("timeout", 60)),
    }


def _stdio_connection(server_name: str, server_config: Dict[str, Any],
                      agent_name: str, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    from shared.utils.platform_only import FEATURE_CODE, may_use
    if not may_use(FEATURE_CODE, agent_name, project_id):
        logger.error(f"Skipping MCP server '{server_name}' for agent '{agent_name}': a platform "
                     f"administrator hasn't enabled code for its workspace")
        return None
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


@asynccontextmanager
async def _open_session(connection: Dict[str, Any]) -> AsyncIterator[ClientSession]:
    """An initialized session with a server. Over HTTP, the SDK's transports follow a
    redirect only within the server's origin."""
    if connection["transport"] == "stdio":
        streams = stdio_client(StdioServerParameters(
            command=connection["command"], args=connection["args"], env=connection["env"]))
        async with streams as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session
    elif connection["transport"] == "sse":
        streams = sse_client(connection["url"], headers=connection["headers"], timeout=connection["timeout"],
                             sse_read_timeout=connection["sse_read_timeout"])
        async with streams as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            yield session
    else:
        timeout = httpx2.Timeout(connection["timeout"], read=connection["sse_read_timeout"])
        async with httpx2.AsyncClient(headers=connection["headers"], timeout=timeout) as client:
            streams = streamable_http_client(connection["url"], http_client=client,
                                             max_sse_event_size=MAX_EVENT_BYTES)
            async with streams as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                yield session


class _ToolError(ToolException):
    """A result the server marked as an error, with its content."""

    def __init__(self, blocks: List[Any]):
        super().__init__("\n".join(b["text"] for b in blocks if b["type"] == "text")
                         or "The MCP tool returned an error without text.")
        self.blocks = blocks or [create_text_block(str(self))]


def _tool_error_blocks(error: ToolException) -> List[Any]:
    if isinstance(error, _ToolError):
        return error.blocks
    raise error


def _block(content: Any) -> Any:
    """An MCP content block as LangChain's."""
    if isinstance(content, types.TextContent):
        return create_text_block(content.text)
    if isinstance(content, types.ImageContent):
        return create_image_block(base64=content.data, mime_type=content.mime_type)
    if isinstance(content, types.ResourceLink):
        create = create_image_block if (content.mime_type or "").startswith("image/") else create_file_block
        return create(url=str(content.uri), mime_type=content.mime_type)
    if isinstance(content, types.EmbeddedResource):
        resource = content.resource
        if isinstance(resource, types.TextResourceContents):
            return create_text_block(resource.text)
        create = create_image_block if (resource.mime_type or "").startswith("image/") else create_file_block
        return create(base64=resource.blob, mime_type=resource.mime_type)
    # Audio, for which LangChain's tool results have no block
    return create_text_block(f"[{content.type} content, {getattr(content, 'mime_type', 'of an unknown kind')}]")


def _langchain_tool(tool: types.Tool, connection: Dict[str, Any]) -> StructuredTool:
    async def call(**arguments: Any) -> Tuple[List[Any], Optional[Dict[str, Any]]]:
        failure: Optional[Exception] = None
        async with _open_session(connection) as session:
            try:
                result = await session.call_tool(tool.name, arguments)
            except Exception as e:
                failure = e  # raised outside: leaving the session may lose it
        if failure is not None:
            raise failure
        blocks = [_block(content) for content in result.content]
        if result.is_error:
            raise _ToolError(blocks)
        structured = result.structured_content
        return blocks, {"structured_content": structured} if structured is not None else None

    metadata = tool.annotations.model_dump() if tool.annotations is not None else {}
    if tool.meta is not None:
        metadata["_meta"] = tool.meta
    return StructuredTool(
        name=tool.name, description=tool.description or "", args_schema=tool.input_schema, coroutine=call,
        response_format="content_and_artifact", metadata=metadata or None, handle_tool_error=_tool_error_blocks)


async def _server_tools(connection: Dict[str, Any]) -> List[StructuredTool]:
    tools: List[types.Tool] = []
    cursor: Optional[str] = None
    async with _open_session(connection) as session:
        for _ in range(MAX_TOOL_PAGES):
            page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
        else:
            raise RuntimeError(f"more than {MAX_TOOL_PAGES} pages of tools")
    return [_langchain_tool(tool, connection) for tool in tools]


async def create_mcp_tools(mcp_servers_config: Any, agent_name: str = "unknown",
                           project_id: Optional[int] = None) -> List[Any]:
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
            connection = _http_connection(server_name, server_config, agent_name, project_id)
        else:
            connection = _stdio_connection(server_name, server_config, agent_name, project_id)
        if connection is not None:
            connections[server_name] = connection

    if not connections:
        return []

    # One server at a time, so a remote server that is down costs only its own
    # tools, as on ADK where each server is its own toolset
    tools: List[Any] = []
    for server_name in connections:
        try:
            tools.extend(await _server_tools(connections[server_name]))
        except Exception as e:
            logger.error(f"Failed to load tools from MCP server '{server_name}' for agent "
                         f"'{agent_name}': {e}. The agent will respond without them.")
    logger.info(f"Created {len(tools)} MCP tools from {len(connections)} server(s) for agent '{agent_name}'")
    return tools
