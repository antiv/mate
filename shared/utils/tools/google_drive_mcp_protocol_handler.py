"""
MCP Protocol Handler for Google Drive Tools
Handles JSON-RPC MCP protocol requests for Google Drive tools
"""

from typing import Dict, Any, List
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import asyncio


class GoogleDriveMCPProtocolHandler:
    """Handles MCP protocol requests for Google Drive tools"""
    
    def __init__(self, gdrive_tools_available: bool = True):
        self.gdrive_tools_available = gdrive_tools_available
    
    def get_mcp_info(self) -> Dict[str, Any]:
        """Get MCP server information"""
        if not self.gdrive_tools_available:
            raise HTTPException(status_code=503, detail="Google Drive MCP server not available")
        
        return {
            "name": "Google Drive MCP Server",
            "version": "1.0.0",
            "description": "Google Drive file management tools via Model Context Protocol",
            "tools": 7,
            "endpoints": {
                "health": "/gdrive/mcp/health",
                "sse": "/gdrive/mcp/sse",
                "tools_list": "/gdrive/mcp/tools/list",
                "tools_call": "/gdrive/mcp/tools/call",
                "initialize": "/gdrive/mcp/initialize"
            },
            "available_tools": [
                "list_files_in_folder",
                "read_google_doc",
                "read_google_doc_by_name",
                "search_files",
                "get_file_metadata",
                "get_file_sharing_permissions",
                "find_by_name"
            ]
        }
    
    def get_mcp_health(self) -> Dict[str, Any]:
        """Get MCP server health status with detailed validation info"""
        if not self.gdrive_tools_available:
            try:
                import os
                has_credentials = bool(os.getenv('GOOGLE_SERVICE_ACCOUNT_INFO'))
                
                return {
                    "status": "unhealthy",
                    "service": "google-drive-mcp-server",
                    "tools": 0,
                    "endpoint": "/gdrive/mcp",
                    "protocol": "MCP",
                    "error": "Google Drive credentials not configured (GOOGLE_SERVICE_ACCOUNT_INFO)",
                    "details": {
                        "has_credentials": has_credentials
                    }
                }
            except Exception as e:
                return {
                    "status": "unhealthy",
                    "service": "google-drive-mcp-server",
                    "tools": 0,
                    "endpoint": "/gdrive/mcp",
                    "protocol": "MCP",
                    "error": f"Validation failed: {str(e)}"
                }
        
        return {
            "status": "healthy",
            "service": "google-drive-mcp-server",
            "tools": 7,
            "endpoint": "/gdrive/mcp",
            "protocol": "MCP"
        }
    
    def get_mcp_initialize(self, request_id: int = 1) -> Dict[str, Any]:
        """Handle MCP initialize request"""
        if not self.gdrive_tools_available:
            raise HTTPException(status_code=503, detail="Google Drive MCP server not available")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "google-drive-mcp-server",
                    "version": "1.0.0"
                }
            }
        }
    
    def get_mcp_tools_list(self, request_id: int = 1) -> Dict[str, Any]:
        """Get list of available Google Drive tools in MCP format"""
        if not self.gdrive_tools_available:
            raise HTTPException(status_code=503, detail="Google Drive MCP server not available")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": self._get_tools_schema()
            }
        }
    
    def _get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get the schema for all Google Drive tools"""
        return [
            {
                "name": "list_files_in_folder",
                "description": "List all files in a Google Drive folder. If folder_id is empty, uses GOOGLE_DRIVE_FOLDER_ID environment variable.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "folder_id": {
                            "type": "string",
                            "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "read_google_doc",
                "description": "Read content from a Google Doc/PDF/text file by file ID",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": "Google Drive file ID"
                        }
                    },
                    "required": ["doc_id"]
                }
            },
            {
                "name": "read_google_doc_by_name",
                "description": "Read content from a Google Doc by searching for its name in a folder",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "doc_name": {
                            "type": "string",
                            "description": "Name of the document to search for"
                        },
                        "folder_id": {
                            "type": "string",
                            "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"
                        }
                    },
                    "required": ["doc_name"]
                }
            },
            {
                "name": "search_files",
                "description": "Search for files in a Google Drive folder using a query",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query to match against file names and content"
                        },
                        "folder_id": {
                            "type": "string",
                            "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "get_file_metadata",
                "description": "Get metadata for a specific Google Drive file",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "Google Drive file ID"
                        }
                    },
                    "required": ["file_id"]
                }
            },
            {
                "name": "get_file_sharing_permissions",
                "description": "Get detailed sharing permissions for a file, including email addresses of users it's shared with",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "Google Drive file ID"
                        }
                    },
                    "required": ["file_id"]
                }
            },
            {
                "name": "find_by_name",
                "description": "Find files by name. Searches for files containing the name in the filename. Returns single match with content, or list for multiple matches.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name or name parts to search for in filenames"
                        },
                        "folder_id": {
                            "type": "string",
                            "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"
                        }
                    },
                    "required": ["name"]
                }
            }
        ]
    
    def _format_result_for_display(self, tool_name: str, result: Dict[str, Any]) -> str:
        """Format tool result for display in MCP clients"""
        import json
        
        if result.get("status") == "error":
            return f"❌ Error: {result.get('error_message', 'Unknown error')}"
        
        output = []
        output.append(f"✅ {result.get('message', 'Operation completed')}\n")
        
        # Format based on what data is available
        if "files" in result:
            files = result["files"]
            if files:
                output.append(f"Files ({len(files)}):")
                for file in files:
                    output.append(f"  • {file['name']} (ID: {file['id']})")
                    output.append(f"    Type: {file.get('mimeType', 'unknown')}, Size: {file.get('size', 'unknown')}, Modified: {file.get('modifiedTime', 'unknown')}")
            else:
                output.append("No files found.")
        
        if "content" in result:
            output.append(f"\n📄 File Content:")
            output.append(f"File: {result.get('doc_name', result.get('file_name', 'unknown'))}")
            content = result["content"]
            # Limit content display to first 2000 chars
            if len(content) > 2000:
                output.append(f"{content[:2000]}...\n[Content truncated, total length: {len(content)} characters]")
            else:
                output.append(content)
        
        if "metadata" in result:
            metadata = result["metadata"]
            output.append(f"\n📋 File Metadata:")
            output.append(f"Name: {metadata.get('name')}")
            output.append(f"ID: {metadata.get('id')}")
            output.append(f"Type: {metadata.get('mimeType')}")
            output.append(f"Size: {metadata.get('size')}")
            output.append(f"Created: {metadata.get('createdTime')}")
            output.append(f"Modified: {metadata.get('modifiedTime')}")
            output.append(f"Owners: {', '.join(metadata.get('owners', []))}")
        
        if "shared_with" in result:
            shared = result["shared_with"]
            output.append(f"\n🔗 Sharing Permissions:")
            output.append(f"File: {result.get('file_name')}")
            output.append(f"Owners: {', '.join(result.get('owners', []))}")
            
            if shared.get("users"):
                output.append(f"\nUsers ({len(shared['users'])}):")
                for user in shared["users"]:
                    output.append(f"  • {user.get('email', user.get('displayName', 'Unknown'))} - {user['role']}")
            
            if shared.get("groups"):
                output.append(f"\nGroups ({len(shared['groups'])}):")
                for group in shared["groups"]:
                    output.append(f"  • {group.get('email', 'Unknown')} - {group['role']}")
            
            if shared.get("domains"):
                output.append(f"\nDomains ({len(shared['domains'])}):")
                for domain in shared["domains"]:
                    output.append(f"  • {domain.get('domain', 'Unknown')} - {domain['role']}")
            
            if shared.get("anyone"):
                output.append(f"\nPublic Access:")
                for anyone in shared["anyone"]:
                    output.append(f"  • Anyone with link - {anyone['role']}")
        
        if "file" in result and result.get("status") == "single_match_found":
            file_info = result["file"]
            output.append(f"\n📄 Matched File:")
            output.append(f"Name: {file_info.get('name')}")
            output.append(f"ID: {file_info.get('id')}")
            output.append(f"Type: {file_info.get('mimeType')}")
            if "content_preview" in result:
                output.append(f"\nContent Preview:")
                output.append(result["content_preview"])
        
        if "matching_files" in result:
            files = result["matching_files"]
            output.append(f"\n📁 Multiple Matches ({len(files)}):")
            for file in files:
                output.append(f"  • {file['name']} (ID: {file['id']})")
                output.append(f"    Type: {file.get('mimeType', 'unknown')}")
        
        if "available_files" in result:
            files = result["available_files"]
            output.append(f"\n📁 Available Files ({len(files)}):")
            for file in files[:20]:  # Limit to first 20
                output.append(f"  • {file['name']} (ID: {file['id']})")
            if len(files) > 20:
                output.append(f"  ... and {len(files) - 20} more files")
        
        return "\n".join(output)
    
    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any], request_id: int = 1) -> Dict[str, Any]:
        """Call a Google Drive tool via MCP protocol"""
        if not self.gdrive_tools_available:
            raise HTTPException(status_code=503, detail="Google Drive MCP server not available")
        
        try:
            # Import Google Drive tools
            from .google_drive_tools import (
                list_files_in_folder,
                read_google_doc,
                read_google_doc_by_name,
                search_files,
                get_file_metadata,
                get_file_sharing_permissions,
                find_by_name
            )
            
            # Create a mock tool context
            class MockToolContext:
                pass
            
            tool_context = MockToolContext()
            
            # Map tool names to functions
            tool_map = {
                "list_files_in_folder": list_files_in_folder,
                "read_google_doc": read_google_doc,
                "read_google_doc_by_name": read_google_doc_by_name,
                "search_files": search_files,
                "get_file_metadata": get_file_metadata,
                "get_file_sharing_permissions": get_file_sharing_permissions,
                "find_by_name": find_by_name
            }
            
            if tool_name not in tool_map:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool '{tool_name}' not found"
                    }
                }
            
            # Call the tool function
            tool_func = tool_map[tool_name]
            
            # Prepare arguments based on tool signature
            if tool_name == "list_files_in_folder":
                result = tool_func(arguments.get("folder_id", ""), tool_context)
            elif tool_name == "read_google_doc":
                result = tool_func(arguments.get("doc_id"), tool_context)
            elif tool_name == "read_google_doc_by_name":
                result = tool_func(arguments.get("doc_name"), arguments.get("folder_id", ""), tool_context)
            elif tool_name == "search_files":
                result = tool_func(arguments.get("query"), arguments.get("folder_id", ""), tool_context)
            elif tool_name == "get_file_metadata":
                result = tool_func(arguments.get("file_id"), tool_context)
            elif tool_name == "get_file_sharing_permissions":
                result = tool_func(arguments.get("file_id"), tool_context)
            elif tool_name == "find_by_name":
                result = tool_func(arguments.get("name"), arguments.get("folder_id", ""), tool_context)
            
            # Format the response with full data
            import json
            formatted_text = self._format_result_for_display(tool_name, result)
            
            content = [
                {
                    "type": "text",
                    "text": formatted_text
                }
            ]
            
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": content,
                    "raw_result": result
                }
            }
        
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }
    
    async def handle_mcp_request(self, request: Request) -> Dict[str, Any]:
        """Handle MCP protocol requests"""
        if not self.gdrive_tools_available:
            raise HTTPException(status_code=503, detail="Google Drive MCP server not available")
        
        try:
            # Parse the JSON-RPC request
            body = await request.json()
            method = body.get("method")
            request_id = body.get("id", 1)
            
            if method == "initialize":
                return self.get_mcp_initialize(request_id)
            
            elif method == "tools/list":
                return self.get_mcp_tools_list(request_id)
            
            elif method == "tools/call":
                # Handle tool execution
                params = body.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                return await self.call_mcp_tool(tool_name, arguments, request_id)
            
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found"
                    }
                }
        
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": body.get("id", 1) if 'body' in locals() else 1,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }
    
    async def get_mcp_sse_stream(self) -> StreamingResponse:
        """Get MCP Server-Sent Events stream"""
        if not self.gdrive_tools_available:
            raise HTTPException(status_code=503, detail="Google Drive MCP server not available")
        
        async def event_generator():
            # Send initial connection event
            yield "event: connected\n"
            yield "data: {\"type\": \"connected\", \"message\": \"Google Drive MCP Server connected\"}\n\n"
            
            # Keep connection alive
            while True:
                await asyncio.sleep(30)  # Send heartbeat every 30 seconds
                yield "event: heartbeat\n"
                yield "data: {\"type\": \"heartbeat\", \"timestamp\": \"" + str(asyncio.get_event_loop().time()) + "\"}\n\n"
        
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Cache-Control"
            }
        )
    
    def get_cors_options_response(self) -> Response:
        """Get CORS preflight response"""
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "86400"
            }
        )
    
    def get_sse_cors_options_response(self) -> Response:
        """Get CORS preflight response for SSE"""
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Cache-Control",
                "Access-Control-Max-Age": "86400"
            }
        )

