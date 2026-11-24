"""
An agent as an MCP server, at /agents/<name>/mcp: one tool that calls it, one prompt that
asks it, and one resource that says what it does.
"""

import hashlib
import json
import logging
import uuid
from typing import Any, Dict, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from ..agent_invoke import run_agent
from .mcp_auth import agent_allowed, mcp_workspace, require_mcp_user
from .served import INVALID_PARAMS, checked_arguments, mcp_user, serve, text_result

logger = logging.getLogger(__name__)

RUN_TIMEOUT_SECONDS = 300.0
# The prefix is how a run is known as MCP's (response_metrics.resolve_origin): an approval
# it asks for is answered in the inbox
SESSION_PREFIX = "mcp_sess_"


def mcp_session_id(user_id: str, agent_name: str, session_id: str) -> str:
    """
    The session of a caller's session_id with an agent. Made from who they are, so a
    caller never picks it, and the same session_id of two callers is two conversations.
    """
    key = json.dumps([user_id, agent_name, session_id]).encode()
    return f"{SESSION_PREFIX}{hashlib.sha256(key).hexdigest()}"


class AgentMCPServer:
    """The MCP server of one agent."""

    def __init__(self, app: FastAPI, agent_name: str, agent_description: Optional[str] = None):
        self.agent_name = agent_name
        self.agent_description = agent_description or f"MCP server for {agent_name} agent"
        self._path = f"/agents/{agent_name}/mcp"
        self._resource_uri = f"mate://agents/{agent_name}/about"
        self._tools = [types.Tool(
            name=f"call_{agent_name}_agent",
            description=f"Call the {agent_name} agent with a message. Returns the agent's response.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1, "description": "The message to send to the agent"},
                    "session_id": {"type": "string",
                                   "description": "Optional: calls with the same session_id continue one conversation"},
                },
                "required": ["message"],
            })]
        server = Server(
            f"agent-mcp-server-{agent_name}", version="1.0.0", title=agent_name,
            description=self.agent_description, instructions=self.agent_description,
            on_list_tools=self._list_tools, on_call_tool=self._call_tool,
            on_list_prompts=self._list_prompts, on_get_prompt=self._get_prompt,
            on_list_resources=self._list_resources, on_read_resource=self._read_resource,
            on_list_resource_templates=self._list_resource_templates)

        async def caller(request: Request, user_id: str = Depends(require_mcp_user)) -> str:
            """Every route but health and CORS preflight: a caller allowed to use this agent."""
            if not agent_allowed(user_id, agent_name, mcp_workspace(request)):
                raise HTTPException(status_code=403, detail=f"Not allowed to use agent {agent_name}")
            return user_id

        serve(app, self._path, f"MCP - Agent: {agent_name}", server, caller, self._info, self._health)

    def _info(self) -> Dict[str, Any]:
        return {
            "name": f"{self.agent_name} Agent MCP Server",
            "version": "1.0.0",
            "description": self.agent_description,
            "tools": 1,
            "endpoints": {
                "health": f"{self._path}/health",
                "tools_list": f"{self._path}/tools/list",
                "tools_call": f"{self._path}/tools/call",
                "initialize": f"{self._path}/initialize",
            },
            "available_tools": [tool.name for tool in self._tools],
        }

    def _health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "service": f"agent-mcp-server-{self.agent_name}",
            "tools": 1,
            "endpoint": self._path,
            "protocol": "MCP",
            "agent_name": self.agent_name,
            "agent_description": self.agent_description,
        }

    async def _list_tools(self, context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self._tools)

    async def _call_tool(self, context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        """Run the agent as the caller."""
        arguments = checked_arguments(self._tools, params)
        user_id = mcp_user(context)
        await self._within_limits(user_id)
        session_id = arguments.get("session_id")
        session = (mcp_session_id(user_id, self.agent_name, session_id) if session_id
                   else f"{SESSION_PREFIX}{uuid.uuid4().hex}")
        try:
            run = await run_agent(self.agent_name, user_id, session, arguments["message"], RUN_TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            return text_result(f"The agent didn't answer within {RUN_TIMEOUT_SECONDS:.0f} seconds.", is_error=True)
        except (httpx.HTTPError, RuntimeError) as e:
            logger.error("MCP call of agent %s failed: %s", self.agent_name, e)
            return text_result(f"Agent call failed: {e}", is_error=True)
        if run.error:
            return text_result(run.error, is_error=True)
        if run.waiting_for:
            return text_result(f"Waiting for approval to use {', '.join(run.waiting_for)}. An administrator decides "
                               f"on MATE's Approvals page; call again with the same session_id afterwards.")
        return text_result(run.reply or "(no response)")

    async def _within_limits(self, user_id: str) -> None:
        """The budgets and request limits /run_sse has in the proxy, which this call doesn't pass."""
        from server.openai_routes import _enforce_rate_limit
        from ..database_client import get_database_client
        from ..models import AgentConfig

        session = get_database_client().get_session()
        try:
            row = session.query(AgentConfig.project_id).filter_by(name=self.agent_name).first() if session else None
        finally:
            if session:
                session.close()
        try:
            await _enforce_rate_limit(user_id, self.agent_name, row[0] if row else None)
        except HTTPException as e:
            raise MCPError(-32000, str(e.detail)) from e

    async def _list_prompts(self, context: Any, params: Any) -> types.ListPromptsResult:
        """One prompt: ask the agent a question through its tool."""
        return types.ListPromptsResult(prompts=[types.Prompt(
            name=f"ask_{self.agent_name}", title=f"Ask {self.agent_name}",
            description=f"Ask the {self.agent_name} agent a question. {self.agent_description}",
            arguments=[types.PromptArgument(name="question", description="What to ask the agent", required=True)])])

    async def _get_prompt(self, context: Any, params: types.GetPromptRequestParams) -> types.GetPromptResult:
        """The prompt's message: tells the client's model to call the agent's tool."""
        if params.name != f"ask_{self.agent_name}":
            raise MCPError(INVALID_PARAMS, f"Prompt '{params.name}' not found. Expected 'ask_{self.agent_name}'")
        question = (params.arguments or {}).get("question")
        if not question:
            raise MCPError(INVALID_PARAMS, "Missing required argument: question")
        text = (f"Use the call_{self.agent_name}_agent tool to ask the {self.agent_name} agent "
                f"the question below, then answer with what the agent says.\n\n{question}")
        return types.GetPromptResult(
            description=f"Ask the {self.agent_name} agent",
            messages=[types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))])

    async def _list_resources(self, context: Any, params: Any) -> types.ListResourcesResult:
        """One resource: what the agent is for and how to call it."""
        return types.ListResourcesResult(resources=[types.Resource(
            uri=self._resource_uri, name=f"About {self.agent_name}",
            description=f"What the {self.agent_name} agent does and how to call it", mime_type="text/markdown")])

    async def _list_resource_templates(self, context: Any, params: Any) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(resource_templates=[])

    async def _read_resource(self, context: Any, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        """The agent's name, description and tool. Not its instruction: that is its system prompt."""
        if str(params.uri) != self._resource_uri:
            raise MCPError(-32002, f"Resource '{params.uri}' not found")
        text = (f"# {self.agent_name}\n\n{self.agent_description}\n\n"
                f"Call it with the `call_{self.agent_name}_agent` tool: pass the question as `message`.\n")
        return types.ReadResourceResult(contents=[types.TextResourceContents(
            uri=self._resource_uri, mime_type="text/markdown", text=text)])
