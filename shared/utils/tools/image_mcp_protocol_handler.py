"""
MCP Protocol Handler for Image Generation Tools
Handles JSON-RPC MCP protocol requests for image generation tools
"""

from typing import Dict, Any, List
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import asyncio


class ImageMCPProtocolHandler:
    """Handles MCP protocol requests for image generation tools"""
    
    def __init__(self, image_tools_available: bool = True):
        self.image_tools_available = image_tools_available
    
    def get_mcp_info(self) -> Dict[str, Any]:
        """Get MCP server information"""
        if not self.image_tools_available:
            raise HTTPException(status_code=503, detail="Image MCP server not available")
        
        return {
            "name": "Image Generation MCP Server",
            "version": "1.0.0",
            "description": "Image generation tools via Model Context Protocol",
            "tools": 2,
            "endpoints": {
                "health": "/images/mcp/health",
                "sse": "/images/mcp/sse",
                "tools_list": "/images/mcp/tools/list",
                "tools_call": "/images/mcp/tools/call",
                "initialize": "/images/mcp/initialize"
            },
            "available_tools": [
                "generate_image_gpt_image_1",
                "generate_image_dall_e_3",
                "generate_image_nano_banana"
            ]
        }
    
    def get_mcp_health(self) -> Dict[str, Any]:
        """Get MCP server health status with detailed validation info"""
        if not self.image_tools_available:
            # Try to get validation details even when not available
            try:
                from .image_tools import validate_image_generation_setup
                is_available, error_message, details = validate_image_generation_setup()
                
                return {
                    "status": "unhealthy",
                    "service": "image-generation-mcp-server",
                    "tools": 0,
                    "endpoint": "/images/mcp",
                    "protocol": "MCP",
                    "error": error_message,
                    "details": details
                }
            except Exception as e:
                return {
                    "status": "unhealthy",
                    "service": "image-generation-mcp-server",
                    "tools": 0,
                    "endpoint": "/images/mcp",
                    "protocol": "MCP",
                    "error": f"Validation failed: {str(e)}"
                }
        
        # Get detailed health info when available
        try:
            from .image_tools import validate_image_generation_setup
            is_available, error_message, details = validate_image_generation_setup()
            
            return {
                "status": "healthy" if is_available else "degraded",
                "service": "image-generation-mcp-server",
                "tools": 3,
                "endpoint": "/images/mcp",
                "protocol": "MCP",
                "validation": {
                    "available": is_available,
                    "error": error_message if not is_available else None,
                    "details": details
                }
            }
        except Exception as e:
            return {
                "status": "healthy",
                "service": "image-generation-mcp-server",
                "tools": 3,
                "endpoint": "/images/mcp",
                "protocol": "MCP",
                "warning": f"Could not validate setup: {str(e)}"
            }
    
    def get_mcp_initialize(self, request_id: int = 1) -> Dict[str, Any]:
        """Handle MCP initialize request"""
        if not self.image_tools_available:
            raise HTTPException(status_code=503, detail="Image MCP server not available")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "image-generation-mcp-server",
                    "version": "1.0.0"
                }
            }
        }
    
    def get_mcp_tools_list(self, request_id: int = 1) -> Dict[str, Any]:
        """Get list of available image generation tools in MCP format"""
        if not self.image_tools_available:
            raise HTTPException(status_code=503, detail="Image MCP server not available")
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": self._get_tools_schema()
            }
        }
    
    def _get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get the schema for all image generation tools"""
        return [
            {
                "name": "generate_image_gpt_image_1",
                "description": "Generate images using GPT Image 1 model",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string", 
                            "description": "The text prompt to generate the image from"
                        },
                        "size": {
                            "type": "string", 
                            "description": "Image size", 
                            "enum": ["256x256", "512x512", "1024x1024"],
                            "default": "1024x1024"
                        },
                        "n": {
                            "type": "integer", 
                            "description": "Number of images to generate", 
                            "default": 1,
                            "minimum": 1,
                            "maximum": 10
                        }
                    },
                    "required": ["prompt"]
                }
            },
            {
                "name": "generate_image_dall_e_3",
                "description": "Generate images using DALL-E 3 model",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string", 
                            "description": "The text prompt to generate the image from"
                        },
                        "size": {
                            "type": "string", 
                            "description": "Image size", 
                            "enum": ["1024x1024", "1024x1792", "1792x1024"],
                            "default": "1024x1024"
                        },
                        "quality": {
                            "type": "string", 
                            "description": "Image quality", 
                            "enum": ["standard", "hd"],
                            "default": "standard"
                        },
                        "n": {
                            "type": "integer", 
                            "description": "Number of images to generate", 
                            "default": 1,
                            "minimum": 1,
                            "maximum": 1
                        }
                    },
                    "required": ["prompt"]
                }
            },
            {
                "name": "generate_image_nano_banana",
                "description": "Generate images using Nano Banana (Gemini 2.0 Flash Exp) model",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string", 
                            "description": "The text prompt to generate the image from"
                        },
                        "asset_name": {
                            "type": "string", 
                            "description": "Name for the asset to track versions",
                            "default": "generated_image"
                        },
                        "model_config": {
                            "type": "object", 
                            "description": "Model configuration parameters (e.g., model name, API settings)",
                            "default": {}
                        }
                    },
                    "required": ["prompt"]
                }
            }
        ]
    
    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any], request_id: int = 1) -> Dict[str, Any]:
        """Call an image generation tool via MCP protocol"""
        if not self.image_tools_available:
            raise HTTPException(status_code=503, detail="Image MCP server not available")
        
        try:
            # Import image tools
            from .image_tools import _generate_image_internal
            
            # Create a mock tool context that doesn't save artifacts
            class MockToolContext:
                async def save_artifact(self, filename: str, content):
                    # Mock implementation that raises an exception to skip artifact saving
                    raise AttributeError("MockToolContext does not support artifact saving")
            
            # Map tool names to models and configurations
            if tool_name == "generate_image_gpt_image_1":
                model = "gpt-image-1"
                model_config = {
                    "size": arguments.get("size", "1024x1024"),
                    "n": arguments.get("n", 1)
                }
            elif tool_name == "generate_image_dall_e_3":
                model = "dall-e-3"
                model_config = {
                    "size": arguments.get("size", "1024x1024"),
                    "quality": arguments.get("quality", "standard"),
                    "n": arguments.get("n", 1)
                }
            elif tool_name == "generate_image_nano_banana":
                # Handle nano banana tool separately
                from .image_tools import generate_image_nano_banana
                
                prompt = arguments.get("prompt")
                if not prompt:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32602,
                            "message": "Missing required parameter: prompt"
                        }
                    }
                
                asset_name = arguments.get("asset_name", "generated_image")
                model_config = arguments.get("model_config", {})
                result = await generate_image_nano_banana(prompt, MockToolContext(), asset_name, model_config)
                
                # Prepare response content
                content = [
                    {
                        "type": "text",
                        "text": f"Image generated successfully using Nano Banana (Gemini 2.0 Flash Exp) model.\nPrompt: {prompt}"
                    }
                ]
                
                # Add inline data if available and artifact saving failed
                if result.get("success") and result.get("inline_data"):
                    content.append({
                        "type": "image",
                        "data": result["inline_data"],
                        "mime_type": result.get("mime_type", "image/png")
                    })
                
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": content,
                        "raw_result": result
                    }
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool '{tool_name}' not found"
                    }
                }
            
            # Call the image generation function
            prompt = arguments.get("prompt")
            if not prompt:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Missing required parameter: prompt"
                    }
                }
            
            result = await _generate_image_internal(prompt, MockToolContext(), model, model_config)
            
            # Prepare response content
            content = [
                {
                    "type": "text",
                    "text": f"Image generated successfully using {model} model.\nPrompt: {prompt}"
                }
            ]
            
            # Add base64 image data if available and artifact saving failed
            if result.get("success") and result.get("base64_data"):
                content.append({
                    "type": "image",
                    "data": result["base64_data"],
                    "mime_type": "image/png"
                })
            
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
        if not self.image_tools_available:
            raise HTTPException(status_code=503, detail="Image MCP server not available")
        
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
        if not self.image_tools_available:
            raise HTTPException(status_code=503, detail="Image MCP server not available")
        
        async def event_generator():
            # Send initial connection event
            yield "event: connected\n"
            yield "data: {\"type\": \"connected\", \"message\": \"Image Generation MCP Server connected\"}\n\n"
            
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
