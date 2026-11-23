"""
MCP Protocol Handler for Agent Tools
Handles JSON-RPC MCP protocol requests for agent execution
"""

from typing import Dict, Any, List, Optional, Union
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import asyncio
import hashlib
import httpx
import os
import json
import time
from shared.utils.utils import get_adk_config
from shared.utils.tools.mcp_jsonrpc import arguments_of, dispatch



def a2a_result_text(result: Any) -> Optional[str]:
    """The text of an A2A message/send result: a message, or a task's artifacts.

    ADK's executor answers with a task whose reply is in its artifacts; a failed
    task carries its error in the status message instead.
    """
    if not isinstance(result, dict):
        return None

    def texts(parts: Any) -> List[str]:
        return [p["text"] for p in parts or [] if isinstance(p, dict) and isinstance(p.get("text"), str)]

    message = result.get("message") if isinstance(result.get("message"), dict) else None
    if message is None and result.get("kind") == "message":
        message = result
    if message is not None:
        found = texts(message.get("parts"))
    else:
        found = [t for artifact in result.get("artifacts") or [] if isinstance(artifact, dict)
                 for t in texts(artifact.get("parts"))]
        if not found:
            status = result.get("status") if isinstance(result.get("status"), dict) else {}
            status_message = status.get("message") if isinstance(status.get("message"), dict) else {}
            found = texts(status_message.get("parts"))
    return "\n".join(found) if found else None


def a2a_task_failed(result_data: Any) -> bool:
    """True when an A2A answer holds a task that failed or was rejected."""
    result = result_data.get("result") if isinstance(result_data, dict) else None
    status = result.get("status") if isinstance(result, dict) else None
    return isinstance(status, dict) and status.get("state") in ("failed", "rejected")


def mcp_context_id(user_id: str, agent_name: str, session_id: str) -> str:
    """
    The A2A context of a caller's session_id with an agent.

    ADK continues whatever conversation has the context id it is given, as the user
    A2A_USER_<context id>. So the caller never picks it: it is made from who they are,
    and the same session_id of two callers is two conversations.
    """
    key = json.dumps([user_id, agent_name, session_id]).encode()
    return f"mcp-{hashlib.sha256(key).hexdigest()}"


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
                    "tools": {},
                    "prompts": {},
                    "resources": {}
                },
                "serverInfo": {
                    "name": f"agent-mcp-server-{self.agent_name}",
                    "title": self.agent_name,
                    "description": self.agent_description,
                    "version": "1.0.0"
                },
                "instructions": self.agent_description
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
    
    @property
    def _prompt_name(self) -> str:
        return f"ask_{self.agent_name}"

    @property
    def _resource_uri(self) -> str:
        return f"mate://agents/{self.agent_name}/about"

    def _result(self, request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        if not self.agent_available:
            raise HTTPException(status_code=503, detail=f"Agent MCP server for {self.agent_name} not available")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error(self, request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def get_mcp_prompts_list(self, request_id: Any = 1) -> Dict[str, Any]:
        """One prompt: ask the agent a question through its tool."""
        return self._result(request_id, {"prompts": [{
            "name": self._prompt_name,
            "title": f"Ask {self.agent_name}",
            "description": f"Ask the {self.agent_name} agent a question. {self.agent_description}",
            "arguments": [{"name": "question", "description": "What to ask the agent",
                           "required": True}],
        }]})

    def get_mcp_prompt(self, name: str, arguments: Dict[str, Any], request_id: Any = 1) -> Dict[str, Any]:
        """The prompt's message: tells the client's model to call the agent's tool."""
        if name != self._prompt_name:
            return self._error(request_id, -32602, f"Prompt '{name}' not found. Expected '{self._prompt_name}'")
        question = (arguments or {}).get("question")
        if not question:
            return self._error(request_id, -32602, "Missing required argument: question")
        return self._result(request_id, {
            "description": f"Ask the {self.agent_name} agent",
            "messages": [{"role": "user", "content": {"type": "text", "text": (
                f"Use the call_{self.agent_name}_agent tool to ask the {self.agent_name} agent "
                f"the question below, then answer with what the agent says.\n\n{question}")}}],
        })

    def get_mcp_resources_list(self, request_id: Any = 1) -> Dict[str, Any]:
        """One resource: what the agent is for and how to call it."""
        return self._result(request_id, {"resources": [{
            "uri": self._resource_uri,
            "name": f"About {self.agent_name}",
            "description": f"What the {self.agent_name} agent does and how to call it",
            "mimeType": "text/markdown",
        }]})

    def read_mcp_resource(self, uri: str, request_id: Any = 1) -> Dict[str, Any]:
        """The agent's name, description and tool. Not its instruction: that is its system prompt."""
        if uri != self._resource_uri:
            return self._error(request_id, -32002, f"Resource '{uri}' not found")
        text = (f"# {self.agent_name}\n\n{self.agent_description}\n\n"
                f"Call it with the `call_{self.agent_name}_agent` tool: pass the question as "
                f"`message`.\n")
        return self._result(request_id, {"contents": [
            {"uri": uri, "mimeType": "text/markdown", "text": text}]})

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
                            "description": "Optional: calls with the same session_id continue one conversation"
                        }
                    },
                    "required": ["message"]
                }
            }
        ]
    
    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any], request_id: int = 1,
                            user_id: str = "") -> Dict[str, Any]:
        """Call an agent via MCP protocol, for the caller user_id"""
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
        if session_id is not None and not isinstance(session_id, str):
            return self._error(request_id, -32602, "session_id must be a string")
        
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
            
            # The caller's conversation of that name; without one, each call is its own
            if session_id and user_id:
                a2a_payload["params"]["message"]["contextId"] = mcp_context_id(
                    user_id, self.agent_name, session_id)
            
            from shared.utils.run_surface import SURFACE_HEADER
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(adk_url, json=a2a_payload,
                                             headers={SURFACE_HEADER: "mcp"})
                
                if response.status_code == 200:
                    result_data = response.json()
                    
                    # Extract response from A2A format
                    content = []
                    if isinstance(result_data, dict):
                        # A2A response format
                        if "result" in result_data:
                            result = result_data["result"]
                            text = a2a_result_text(result)
                            if text:
                                content.append({
                                    "type": "text",
                                    "text": text
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
                            # A failed run comes back as a task with the error in its
                            # text: the client must not take it for the agent's answer
                            "isError": a2a_task_failed(result_data),
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
    
    async def handle_mcp_request(self, request: Request, user_id: str = "") -> Union[Dict[str, Any], Response]:
        """Answer an MCP message posted to the server's endpoint by user_id"""
        return await dispatch(request, {
            "initialize": lambda params, request_id: self.get_mcp_initialize(request_id),
            "tools/list": lambda params, request_id: self.get_mcp_tools_list(request_id),
            "tools/call": lambda params, request_id: self.call_mcp_tool(
                params.get("name"), arguments_of(params), request_id, user_id),
            "prompts/list": lambda params, request_id: self.get_mcp_prompts_list(request_id),
            "prompts/get": lambda params, request_id: self.get_mcp_prompt(
                params.get("name"), arguments_of(params), request_id),
            "resources/list": lambda params, request_id: self.get_mcp_resources_list(request_id),
            "resources/templates/list": lambda params, request_id: self._result(
                request_id, {"resourceTemplates": []}),
            "resources/read": lambda params, request_id: self.read_mcp_resource(params.get("uri"), request_id),
        })

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
