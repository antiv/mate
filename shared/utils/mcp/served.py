"""
MATE's own MCP servers (agents, Image Generation, Google Drive) over HTTP: the MCP SDK's
server behind MATE's routes and authentication.

Each request is answered on its own, as JSON: the servers keep no session and send
nothing of their own. The SDK reads the messages, negotiates the protocol version and
answers ping and notifications; a server gives it the handlers of what it offers.
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError
from mcp import types

logger = logging.getLogger(__name__)

# It logs the start and the end of each manager, and there is one per request
logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)

INVALID_PARAMS = -32602
_USER = "mate_mcp_user"


def mcp_user(context: Any) -> str:
    """The caller of the request a handler is answering."""
    return context.request.scope[_USER]


def checked_arguments(tools: List[types.Tool], params: types.CallToolRequestParams) -> Dict[str, Any]:
    """The arguments of a call, when the tool exists and takes them; else MCPError."""
    tool = next((t for t in tools if t.name == params.name), None)
    if tool is None:
        raise MCPError(INVALID_PARAMS, f"Tool '{params.name}' not found")
    arguments = params.arguments or {}
    wrong = next(iter(sorted(Draft202012Validator(tool.input_schema).iter_errors(arguments), key=str)), None)
    if wrong is not None:
        where = ".".join(str(p) for p in wrong.absolute_path)
        raise MCPError(INVALID_PARAMS, f"{where}: {wrong.message}" if where else wrong.message)
    return arguments


def text_result(text: str, is_error: bool = False, structured: Optional[Dict[str, Any]] = None) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                is_error=is_error, structured_content=structured)


class _Answer(Response):
    """Hands the request to the SDK, as the caller."""

    def __init__(self, server: Server, user: str, body: Optional[bytes] = None) -> None:
        super().__init__()
        self._server, self._user, self._given = server, user, body

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        # The SDK asks for both; curl and older clients name neither
        headers = [(k, v) for k, v in scope["headers"] if k != b"accept"]
        headers.append((b"accept", b"application/json, text/event-stream"))
        if self._given is not None:
            headers = [(k, v) for k, v in headers if k not in (b"content-type", b"content-length")]
            headers += [(b"content-type", b"application/json"), (b"content-length", str(len(self._given)).encode())]
            unread = [{"type": "http.request", "body": self._given, "more_body": False}]
            original = receive

            async def receive() -> Any:
                return unread.pop() if unread else await original()

        manager = StreamableHTTPSessionManager(app=self._server, json_response=True, stateless=True)
        async with manager.run():
            await manager.handle_request({**scope, "headers": headers, _USER: self._user}, receive, send)


def _wants_stream(request: Request) -> bool:
    """True for the GET by which an MCP client asks to open a stream, as opposed to a
    browser or curl asking for the server's information."""
    accept = request.headers.get("accept", "").lower()
    return "text/event-stream" in accept and "application/json" not in accept and "text/html" not in accept


async def _message(request: Request, method: str) -> bytes:
    """The body of /initialize, /tools/list or /tools/call as a message of that method."""
    try:
        body = json.loads(await request.body() or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = {}
    params = body.get("params") if isinstance(body, dict) else None
    params = dict(params) if isinstance(params, dict) else {}
    if method == "initialize":
        for name, value in (("protocolVersion", "2024-11-05"), ("capabilities", {}),
                            ("clientInfo", {"name": "http", "version": "0"})):
            params.setdefault(name, value)
    request_id = body.get("id") if isinstance(body, dict) else None
    return json.dumps({"jsonrpc": "2.0", "id": 1 if request_id is None else request_id,
                       "method": method, "params": params}).encode()


def serve(app: FastAPI, path: str, tag: str, server: Server, caller: Callable[..., Any],
          info: Callable[[], Dict[str, Any]], health: Callable[[], Dict[str, Any]],
          available: Callable[[], bool] = lambda: True) -> None:
    """
    The routes of one MCP server.

    POST <path> is the MCP endpoint. GET <path> tells a person about the server, and a
    client that asks it for a stream gets 405. POST /initialize, /tools/list and
    /tools/call take the parameters of that method alone. `caller` is the dependency that
    authenticates; GET /health is open. While the server isn't available, all but
    /health answer 503.
    """

    async def user_of(request: Request, user: str = Depends(caller)) -> str:
        if not available():
            raise HTTPException(status_code=503, detail=f"The MCP server at {path} is not available")
        return user

    @app.get(f"{path}/health", tags=[tag])
    async def mcp_health() -> Dict[str, Any]:
        """MCP server health check (no auth required)"""
        return health()

    @app.get(f"{path}", tags=[tag])
    async def mcp_info(request: Request, user: str = Depends(user_of)) -> Any:
        """MCP server info endpoint; 405 for a client that asks for a stream"""
        if _wants_stream(request):
            return Response(status_code=405, headers={"Allow": "POST"})
        return info()

    @app.post(f"{path}", tags=[tag], response_class=Response)
    async def mcp_endpoint(request: Request, user: str = Depends(user_of)) -> Response:
        """The MCP endpoint: streamable HTTP, each message answered with JSON"""
        return _Answer(server, user)

    @app.post(f"{path}/initialize", tags=[tag], response_class=Response)
    async def mcp_initialize(request: Request, user: str = Depends(user_of)) -> Response:
        """MCP initialize, without a client"""
        return _Answer(server, user, await _message(request, "initialize"))

    @app.post(f"{path}/tools/list", tags=[tag], response_class=Response)
    async def mcp_list_tools(request: Request, user: str = Depends(user_of)) -> Response:
        """MCP tools/list, without a client"""
        return _Answer(server, user, await _message(request, "tools/list"))

    @app.post(f"{path}/tools/call", tags=[tag], response_class=Response)
    async def mcp_call_tool(request: Request, user: str = Depends(user_of)) -> Response:
        """MCP tools/call, without a client"""
        return _Answer(server, user, await _message(request, "tools/call"))

    @app.options(f"{path}")
    async def mcp_options() -> Response:
        """Handle CORS preflight requests"""
        return Response(status_code=200, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept, Mcp-Protocol-Version",
            "Access-Control-Max-Age": "86400",
        })
