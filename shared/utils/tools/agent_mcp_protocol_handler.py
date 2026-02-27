"""
MCP Protocol Handler for Agent Tools
Handles JSON-RPC MCP protocol requests for agent execution
"""

from typing import Dict, Any, List
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import asyncio
import httpx
import os
import json
import time
from shared.utils.utils import get_adk_config


class AgentMCPProtocolHandler:
    """Handles MCP protocol requests for agent execution"""
    
    def __init__(self, agent_name: str, agent_description: str = None, agent_available: bool = True):
        self.agent_name = agent_name
        self.agent_description = agent_description or f"MCP server for {agent_name} agent"
        self.agent_available = agent_available
        self.adk_config = get_adk_config()
        self.adk_host = self.adk_config.get('adk_host', 'localhost')
        self.adk_port = self.adk_config.get('adk_port', 8001)
    
    def get_mcp_info(self) -> Dict[str, Any]:
        """Get MCP server information"""
        if not self.agent_available:
            raise HTTPException(status_code=503, detail=f"Agent MCP server for {self.agent_name} not available")
        
        base_path = f"/agents/{self.agent_name}/mcp"
        return {
            "name": f"{self.agent_name} Agent MCP Server",
            "version": "1.0.0",
            "description": self.agent_description,
            "tools": 1,
            "endpoints": {
                "health": f"{base_path}/health",
                "sse": f"{base_path}/sse",
                "tools_list": f"{base_path}/tools/list",
                "tools_call": f"{base_path}/tools/call",
                "initialize": f"{base_path}/initialize"
            },
            "available_tools": [
                f"call_{self.agent_name}_agent"
            ]
        }
    
    def get_mcp_health(self) -> Dict[str, Any]:
        """Get MCP server health status"""
        return {
            "status": "healthy" if self.agent_available else "unhealthy",
            "service": f"agent-mcp-server-{self.agent_name}",
            "tools": 1 if self.agent_available else 0,
            "endpoint": f"/agents/{self.agent_name}/mcp",
            "protocol": "MCP",
            "agent_name": self.agent_name,
            "agent_description": self.agent_description
        }
    
    def get_mcp_initialize(self, request_id: int = 1) -> Dict[str, Any]:
        """Handle MCP initialize request"""
        if not self.agent_available:
            raise HTTPException(status_code=503, detail=f"Agent MCP server for {self.agent_name} not available")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": f"agent-mcp-server-{self.agent_name}",
                    "version": "1.0.0"
                }
            }
        }
    
    def get_mcp_tools_list(self, request_id: int = 1) -> Dict[str, Any]:
        """Get list of available agent tools in MCP format"""
        if not self.agent_available:
            raise HTTPException(status_code=503, detail=f"Agent MCP server for {self.agent_name} not available")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": self._get_tools_schema()
            }
        }
    
    def _get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get the schema for agent tool"""
        return [
            {
                "name": f"call_{self.agent_name}_agent",
                "description": f"Call the {self.agent_name} agent with a message. Returns the agent's response.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message to send to the agent"
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Optional session ID for maintaining conversation context"
                        }
                    },
                    "required": ["message"]
                }
            }
        ]
    
    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any], request_id: int = 1) -> Dict[str, Any]:
        """Call an agent via MCP protocol"""
        if not self.agent_available:
            raise HTTPException(status_code=503, detail=f"Agent MCP server for {self.agent_name} not available")
        
        expected_tool_name = f"call_{self.agent_name}_agent"
        if tool_name != expected_tool_name:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool '{tool_name}' not found. Expected '{expected_tool_name}'"
                }
            }
        
        message = arguments.get("message")
        if not message:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32602,
                    "message": "Missing required parameter: message"
                }
            }
        
        session_id = arguments.get("session_id")
        
        try:
            # Call agent via A2A endpoint on ADK server
            # Use A2A protocol format
            import uuid
            adk_url = f"http://{self.adk_host}:{self.adk_port}/a2a/{self.agent_name}"
            
            # Prepare A2A JSON-RPC payload
            a2a_payload = {
                "jsonrpc": "2.0",
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [
                            {
                                "kind": "text",
                                "text": message
                            }
                        ]
                    }
                },
                "id": 1
            }
            
            # Add session context if provided
            if session_id:
                a2a_payload["params"]["sessionId"] = session_id
            
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(adk_url, json=a2a_payload)
                
                if response.status_code == 200:
                    result_data = response.json()
                    
                    # Extract response from A2A format
                    content = []
                    if isinstance(result_data, dict):
                        # A2A response format
                        if "result" in result_data:
                            result = result_data["result"]
                            if "message" in result and "parts" in result["message"]:
                                # Extract text from parts
                                text_parts = []
                                for part in result["message"]["parts"]:
                                    if part.get("kind") == "text" and "text" in part:
                                        text_parts.append(part["text"])
                                if text_parts:
                                    content.append({
                                        "type": "text",
                                        "text": "\n".join(text_parts)
                                    })
                            else:
                                # Fallback: use result as text
                                content.append({
                                    "type": "text",
                                    "text": str(result)
                                })
                        elif "error" in result_data:
                            error_msg = result_data["error"].get("message", "Unknown error")
                            return {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {
                                    "code": result_data["error"].get("code", -32603),
                                    "message": f"Agent error: {error_msg}"
                                }
                            }
                        else:
                            # Fallback: try to extract text from response
                            text_response = result_data.get("response", result_data.get("text", str(result_data)))
                            content.append({
                                "type": "text",
                                "text": str(text_response)
                            })
                    else:
                        content.append({
                            "type": "text",
                            "text": str(result_data)
                        })
                    
                    if not content:
                        content.append({
                            "type": "text",
                            "text": "Agent responded successfully but no text content was returned."
                        })
                    
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": content,
                            "raw_result": result_data
                        }
                    }
                else:
                    error_text = response.text
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Agent call failed (HTTP {response.status_code}): {error_text}"
                        }
                    }
                    
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal error calling agent: {str(e)}"
                }
            }
    
    async def handle_mcp_request(self, request: Request) -> Dict[str, Any]:
        """Handle generic MCP protocol request"""
        try:
            body = await request.json()
            method = body.get("method")
            params = body.get("params", {})
            request_id = body.get("id", 1)
            
            if method == "initialize":
                return self.get_mcp_initialize(request_id)
            elif method == "tools/list":
                return self.get_mcp_tools_list(request_id)
            elif method == "tools/call":
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
        """Get Server-Sent Events stream for real-time communication"""
        if not self.agent_available:
            raise HTTPException(status_code=503, detail=f"Agent MCP server for {self.agent_name} not available")
        
        async def event_generator():
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connection', 'status': 'connected', 'agent': self.agent_name})}\n\n"
            
            # Keep connection alive
            while True:
                await asyncio.sleep(30)
                yield f"data: {json.dumps({'type': 'ping', 'timestamp': time.time()})}\n\n"
        
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
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
                "Access-Control-Max-Age": "3600"
            }
        )
    
    def get_sse_cors_options_response(self) -> Response:
        """Get CORS preflight response for SSE"""
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "3600"
            }
        )
