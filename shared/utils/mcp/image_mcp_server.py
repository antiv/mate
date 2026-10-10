"""
Image MCP Server Implementation
Handles Image MCP endpoints and protocol requests
"""

from fastapi import Depends, FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
from ..tools.image_mcp_protocol_handler import ImageMCPProtocolHandler


def _require_user(request: Request) -> str:
    """Every route but health and CORS preflight needs a signed-in caller.

    The tools spend the server's provider keys; they were reachable by anyone.
    """
    from server.auth import get_dashboard_auth_user
    user = get_dashboard_auth_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required",
                            headers={"WWW-Authenticate": "Basic"})
    return user


# What an unauthenticated health check may see: not which model or keys are set
_PUBLIC_HEALTH_KEYS = ("status", "service", "tools", "endpoint", "protocol")


class ImageMCPServer:
    """Image MCP Server with endpoint registration"""
    
    def __init__(self, app: FastAPI, image_mcp_available: bool = True):
        self.app = app
        self.image_mcp_available = image_mcp_available
        self.mcp_handler = ImageMCPProtocolHandler(image_mcp_available)
        self._register_endpoints()
    
    def _register_endpoints(self):
        """Register all Image MCP endpoints"""
        
        @self.app.get("/images/mcp/health", tags=["MCP - Images"])
        async def image_mcp_health_check():
            """Image MCP server health check (no auth required, so no setup details)"""
            health = self.mcp_handler.get_mcp_health()
            return {k: health[k] for k in _PUBLIC_HEALTH_KEYS if k in health}

        @self.app.post("/images/mcp/initialize", tags=["MCP - Images"])
        async def image_mcp_initialize(request: Request, user: str = Depends(_require_user)):
            """Image MCP protocol initialize endpoint"""
            return self.mcp_handler.get_mcp_initialize()

        @self.app.get("/images/mcp/sse", tags=["MCP - Images"])
        async def image_mcp_sse(request: Request, user: str = Depends(_require_user)):
            """Image MCP Server-Sent Events endpoint for real-time communication"""
            return await self.mcp_handler.get_mcp_sse_stream()

        @self.app.post("/images/mcp/tools/list", tags=["MCP - Images"])
        async def image_mcp_list_tools(request: Request, user: str = Depends(_require_user)):
            """Image MCP protocol tools/list endpoint"""
            return self.mcp_handler.get_mcp_tools_list()

        @self.app.post("/images/mcp/tools/call", tags=["MCP - Images"])
        async def image_mcp_call_tool(request: Request, user: str = Depends(_require_user)):
            """Image MCP protocol tools/call endpoint"""
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

        @self.app.get("/images/mcp", tags=["MCP - Images"])
        async def image_mcp_info(user: str = Depends(_require_user)):
            """Image MCP server info endpoint"""
            return self.mcp_handler.get_mcp_info()

        @self.app.post("/images/mcp", tags=["MCP - Images"])
        async def image_mcp_protocol_handler(request: Request, user: str = Depends(_require_user)):
            """Handle Image MCP protocol requests at the root /images/mcp endpoint"""
            return await self.mcp_handler.handle_mcp_request(request)

        @self.app.options("/images/mcp")
        async def image_mcp_options():
            """Handle CORS preflight requests"""
            return self.mcp_handler.get_cors_options_response()

        @self.app.options("/images/mcp/sse")
        async def image_mcp_sse_options():
            """Handle CORS preflight requests for SSE"""
            return self.mcp_handler.get_sse_cors_options_response()
    
    def check_image_mcp_availability(self) -> bool:
        """Check if Image MCP tools are available and update handler"""
        try:
            # Import validation function
            from ..tools.image_tools import validate_image_generation_setup
            
            # Run actual validation
            is_available, error_message, details = validate_image_generation_setup()
            
            if is_available:
                self.image_mcp_available = True
                self.mcp_handler = ImageMCPProtocolHandler(True)
                print("✅ Image MCP tools initialized successfully")
                print(f"   Default image model: {details.get('default_model')} "
                      f"({details.get('provider')}, from {details.get('source')})")
                return True
            else:
                print(f"⚠️  Image MCP tools not available: {error_message}")
                self.image_mcp_available = False
                self.mcp_handler = ImageMCPProtocolHandler(False)
                return False
            
        except Exception as e:
            print(f"⚠️  Image MCP tools validation error: {e}")
            self.image_mcp_available = False
            self.mcp_handler = ImageMCPProtocolHandler(False)
            return False
    
    def recheck_availability(self) -> bool:
        """Re-check image MCP availability and update status"""
        return self.check_image_mcp_availability()
