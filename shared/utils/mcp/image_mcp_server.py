"""
The Image Generation MCP server, at /images/mcp: generates images with the server's provider keys.
"""

import base64
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from mcp import types
from mcp.server.lowlevel import Server

from .served import checked_arguments, serve, text_result

TOOLS = [
    types.Tool(
        name="generate_image_gpt_image_1",
        description="Generate images using GPT Image 1 model",
        input_schema={"type": "object", "properties": {
            "prompt": {"type": "string", "description": "The text prompt to generate the image from"},
            "size": {"type": "string", "description": "Image size", "enum": ['256x256', '512x512', '1024x1024'], "default": "1024x1024"},
            "n": {"type": "integer", "description": "Number of images to generate", "default": 1, "minimum": 1, "maximum": 10},
        }, "required": ["prompt"]}),
    types.Tool(
        name="generate_image_dall_e_3",
        description="Generate images using DALL-E 3 model",
        input_schema={"type": "object", "properties": {
            "prompt": {"type": "string", "description": "The text prompt to generate the image from"},
            "size": {"type": "string", "description": "Image size", "enum": ['1024x1024', '1024x1792', '1792x1024'], "default": "1024x1024"},
            "quality": {"type": "string", "description": "Image quality", "enum": ['standard', 'hd'], "default": "standard"},
            "n": {"type": "integer", "description": "Number of images to generate", "default": 1, "minimum": 1, "maximum": 1},
        }, "required": ["prompt"]}),
    types.Tool(
        name="generate_image_nano_banana",
        description="Generate images using Nano Banana (Gemini 2.5 Flash Image) model",
        input_schema={"type": "object", "properties": {
            "prompt": {"type": "string", "description": "The text prompt to generate the image from"},
            "asset_name": {"type": "string", "description": "Name for the asset to track versions", "default": "generated_image"},
        }, "required": ["prompt"]}),
]
# The model of each tool, and the request parameters it takes from the caller with their
# defaults. No others: a caller could pick any model and parameters on the server's keys.
MODELS: Dict[str, Any] = {
    "generate_image_gpt_image_1": ("gpt-image-1", {"size": "1024x1024", "n": 1}),
    "generate_image_dall_e_3": ("dall-e-3", {"size": "1024x1024", "quality": "standard", "n": 1}),
    "generate_image_nano_banana": ("nano-banana", {}),
}


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


class _Images:
    """A tool context that keeps the images for the answer instead of saving them: the
    image tool gives a path, never the data, and an MCP caller has no session to look in."""

    def __init__(self) -> None:
        self.saved: List[types.ImageContent] = []

    async def save_artifact(self, filename: str, content: Any) -> int:
        blob = content.inline_data
        self.saved.append(types.ImageContent(type="image", data=base64.b64encode(blob.data).decode(),
                                             mime_type=blob.mime_type or "image/png"))
        return 0


class ImageMCPServer:
    """The Image Generation MCP server."""

    def __init__(self, app: FastAPI, image_mcp_available: bool = True):
        self.image_mcp_available = image_mcp_available
        server = Server("image-generation-mcp-server", version="1.0.0",
                        on_list_tools=self._list_tools, on_call_tool=self._call_tool)
        serve(app, "/images/mcp", "MCP - Images", server, _require_user, self._info, self._health,
              lambda: self.image_mcp_available)

    def _info(self) -> Dict[str, Any]:
        return {
            "name": "Image Generation MCP Server",
            "version": "1.0.0",
            "description": "Image generation tools via Model Context Protocol",
            "tools": len(TOOLS),
            "endpoints": {
                "health": "/images/mcp/health",
                "tools_list": "/images/mcp/tools/list",
                "tools_call": "/images/mcp/tools/call",
                "initialize": "/images/mcp/initialize",
            },
            "available_tools": [tool.name for tool in TOOLS],
        }

    def _health(self) -> Dict[str, Any]:
        """Open to anyone, so not which model or keys are set."""
        status = "unhealthy"
        if self.image_mcp_available:
            from ..tools.image_tools import validate_image_generation_setup
            try:
                status = "healthy" if validate_image_generation_setup()[0] else "degraded"
            except Exception:
                status = "healthy"  # as far as was checked at startup
        return {
            "status": status,
            "service": "image-generation-mcp-server",
            "tools": len(TOOLS) if self.image_mcp_available else 0,
            "endpoint": "/images/mcp",
            "protocol": "MCP",
        }

    async def _list_tools(self, context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=TOOLS)

    async def _call_tool(self, context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = checked_arguments(TOOLS, params)
        from ..tools import image_tools

        model, defaults = MODELS[params.name]
        config = {name: arguments.get(name, default) for name, default in defaults.items()}
        images = _Images()
        result = await image_tools._generate_image_internal(arguments["prompt"], images, model, config)
        if not result.get("success"):
            return text_result(str(result.get("error") or "No image was generated."), is_error=True)
        text = f"Image generated with {model}.\nPrompt: {arguments['prompt']}"
        if not images.saved and result.get("url"):
            text += f"\n{result['url']}"
        return types.CallToolResult(content=[types.TextContent(type="text", text=text), *images.saved])

    def check_image_mcp_availability(self) -> bool:
        """Whether an image can be generated with the keys set; the server answers 503 without."""
        try:
            from ..tools.image_tools import validate_image_generation_setup
            self.image_mcp_available, error_message, details = validate_image_generation_setup()
        except Exception as e:
            self.image_mcp_available, error_message, details = False, f"validation error: {e}", {}
        if self.image_mcp_available:
            print("✅ Image MCP tools initialized successfully")
            print(f"   Default image model: {details.get('default_model')} "
                  f"({details.get('provider')}, from {details.get('source')})")
        else:
            print(f"⚠️  Image MCP tools not available: {error_message}")
        return self.image_mcp_available
