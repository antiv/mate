"""
Google Drive MCP Server Implementation
Handles Google Drive MCP endpoints and protocol requests
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
from ..tools.google_drive_mcp_protocol_handler import GoogleDriveMCPProtocolHandler


class GoogleDriveMCPServer:
    """Google Drive MCP Server with endpoint registration"""
    
    def __init__(self, app: FastAPI, gdrive_mcp_available: bool = True):
        self.app = app
        self.gdrive_mcp_available = gdrive_mcp_available
        self.mcp_handler = GoogleDriveMCPProtocolHandler(gdrive_mcp_available)
        self._register_endpoints()
    
    def _register_endpoints(self):
        """Register all Google Drive MCP endpoints"""
        
        @self.app.get("/gdrive/mcp/health", tags=["MCP - Google Drive"])
        async def gdrive_mcp_health_check():
            """Google Drive MCP server health check (no auth required)"""
            return self.mcp_handler.get_mcp_health()

        @self.app.post("/gdrive/mcp/initialize", tags=["MCP - Google Drive"])
        async def gdrive_mcp_initialize(request: Request):
            """Google Drive MCP protocol initialize endpoint"""
            return self.mcp_handler.get_mcp_initialize()

        @self.app.get("/gdrive/mcp/sse", tags=["MCP - Google Drive"])
        async def gdrive_mcp_sse(request: Request):
            """Google Drive MCP Server-Sent Events endpoint for real-time communication"""
            return await self.mcp_handler.get_mcp_sse_stream()

        @self.app.post("/gdrive/mcp/tools/list", tags=["MCP - Google Drive"])
        async def gdrive_mcp_list_tools(request: Request):
            """Google Drive MCP protocol tools/list endpoint"""
            return self.mcp_handler.get_mcp_tools_list()

        @self.app.post("/gdrive/mcp/tools/call", tags=["MCP - Google Drive"])
        async def gdrive_mcp_call_tool(request: Request):
            """Google Drive MCP protocol tools/call endpoint"""
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

        @self.app.get("/gdrive/mcp", tags=["MCP - Google Drive"])
        async def gdrive_mcp_info():
            """Google Drive MCP server info endpoint"""
            return self.mcp_handler.get_mcp_info()

        @self.app.post("/gdrive/mcp", tags=["MCP - Google Drive"])
        async def gdrive_mcp_protocol_handler(request: Request):
            """Handle Google Drive MCP protocol requests at the root /gdrive/mcp endpoint"""
            return await self.mcp_handler.handle_mcp_request(request)

        @self.app.options("/gdrive/mcp")
        async def gdrive_mcp_options():
            """Handle CORS preflight requests"""
            return self.mcp_handler.get_cors_options_response()

        @self.app.options("/gdrive/mcp/sse")
        async def gdrive_mcp_sse_options():
            """Handle CORS preflight requests for SSE"""
            return self.mcp_handler.get_sse_cors_options_response()
    
    def check_gdrive_mcp_availability(self) -> bool:
        """Check if Google Drive MCP tools are available and update handler"""
        try:
            import os
            
            # Check if credentials are configured
            has_service_account_info = bool(os.getenv('GOOGLE_SERVICE_ACCOUNT_INFO'))
            
            if has_service_account_info:
                self.gdrive_mcp_available = True
                self.mcp_handler = GoogleDriveMCPProtocolHandler(True)
                print("✅ Google Drive MCP tools initialized successfully")
                print("   Credentials: GOOGLE_SERVICE_ACCOUNT_INFO environment variable")
                return True
            else:
                print("⚠️  Google Drive MCP tools not available: No credentials configured")
                print("   Set GOOGLE_SERVICE_ACCOUNT_INFO environment variable")
                self.gdrive_mcp_available = False
                self.mcp_handler = GoogleDriveMCPProtocolHandler(False)
                return False
            
        except Exception as e:
            print(f"⚠️  Google Drive MCP tools validation error: {e}")
            self.gdrive_mcp_available = False
            self.mcp_handler = GoogleDriveMCPProtocolHandler(False)
            return False
    
    def recheck_availability(self) -> bool:
        """Re-check Google Drive MCP availability and update status"""
        return self.check_gdrive_mcp_availability()

