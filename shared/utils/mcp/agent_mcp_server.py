"""
Agent MCP Server Implementation
Dynamically creates MCP endpoints for agents from database configuration
"""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from ..tools.agent_mcp_protocol_handler import AgentMCPProtocolHandler
from .mcp_auth import agent_allowed, require_mcp_user
import logging

logger = logging.getLogger(__name__)


class AgentMCPServer:
    """Agent MCP Server with dynamic endpoint registration"""
    
    def __init__(self, app: FastAPI, agent_name: str, agent_description: str = None):
        self.app = app
        self.agent_name = agent_name
        self.agent_description = agent_description or f"MCP server for {agent_name} agent"
        self.mcp_handler = AgentMCPProtocolHandler(agent_name, agent_description, True)
        self._register_endpoints()
    
    def _register_endpoints(self):
        """Register all agent MCP endpoints"""
        base_path = f"/agents/{self.agent_name}/mcp"

        async def require_user(user_id: str = Depends(require_mcp_user)) -> str:
            """Every route but health and CORS preflight: a caller allowed to use this agent."""
            if not agent_allowed(user_id, self.agent_name):
                raise HTTPException(status_code=403,
                                    detail=f"Not allowed to use agent {self.agent_name}")
            return user_id
        
        @self.app.get(f"{base_path}/health", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_health_check():
            """Agent MCP server health check (no auth required)"""
            return self.mcp_handler.get_mcp_health()

        @self.app.post(f"{base_path}/initialize", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_initialize(request: Request, user: str = Depends(require_user)):
            """Agent MCP protocol initialize endpoint"""
            return self.mcp_handler.get_mcp_initialize()

        @self.app.get(f"{base_path}/sse", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_sse(request: Request, user: str = Depends(require_user)):
            """Agent MCP Server-Sent Events endpoint for real-time communication"""
            return await self.mcp_handler.get_mcp_sse_stream()

        @self.app.post(f"{base_path}/tools/list", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_list_tools(request: Request, user: str = Depends(require_user)):
            """Agent MCP protocol tools/list endpoint"""
            return self.mcp_handler.get_mcp_tools_list()

        @self.app.post(f"{base_path}/tools/call", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_call_tool(request: Request, user: str = Depends(require_user)):
            """Agent MCP protocol tools/call endpoint"""
            try:
                body = await request.json()
                params = body.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                request_id = body.get("id", 1)
                return await self.mcp_handler.call_mcp_tool(tool_name, arguments, request_id)
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": body.get("id", 1) if 'body' in locals() else 1,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }

        @self.app.get(f"{base_path}", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_info(user: str = Depends(require_user)):
            """Agent MCP server info endpoint"""
            return self.mcp_handler.get_mcp_info()

        @self.app.post(f"{base_path}", tags=[f"MCP - Agent: {self.agent_name}"])
        async def agent_mcp_protocol_handler(request: Request, user: str = Depends(require_user)):
            """Handle Agent MCP protocol requests at the root endpoint"""
            return await self.mcp_handler.handle_mcp_request(request)

        @self.app.options(f"{base_path}")
        async def agent_mcp_options():
            """Handle CORS preflight requests"""
            return self.mcp_handler.get_cors_options_response()

        @self.app.options(f"{base_path}/sse")
        async def agent_mcp_sse_options():
            """Handle CORS preflight requests for SSE"""
            return self.mcp_handler.get_sse_cors_options_response()
