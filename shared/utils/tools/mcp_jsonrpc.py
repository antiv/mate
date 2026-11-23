"""
The JSON-RPC side of MATE's own MCP servers (agents, Image Generation, Google Drive):
what a POST to their endpoint means, whichever server it is. Each protocol handler
gives dispatch() the methods it serves.

A server sends nothing on its own, so there is no stream to open: wants_stream() is
for the GET an MCP client uses to ask for one, which gets 405.
"""

import inspect
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Union

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

Answer = Dict[str, Any]
# (params, request id) -> the JSON-RPC answer
Method = Callable[[Dict[str, Any], Any], Union[Answer, Awaitable[Answer]]]


def error(request_id: Any, code: int, message: str) -> Answer:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def dispatch(request: Request, methods: Dict[str, Method]) -> Union[Answer, Response]:
    """
    Answer one JSON-RPC message.

    - A notification ("notifications/...") and a response of the client take no
      answer: 202 with no body.
    - ping gets an empty result.
    - A body that isn't JSON, or isn't one message, gets 400.
    - A request without an id is answered with id 1, as these servers always did,
      though JSON-RPC would leave it unanswered.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(error(None, PARSE_ERROR, "Parse error: the body is not JSON"), status_code=400)
    if not isinstance(body, dict):
        return JSONResponse(error(None, INVALID_REQUEST, "Expected one JSON-RPC message; batches are not supported"),
                            status_code=400)

    method = body.get("method")
    request_id = body.get("id", 1)
    if method is None and ("result" in body or "error" in body):
        return Response(status_code=202)
    if not isinstance(method, str):
        return JSONResponse(error(request_id, INVALID_REQUEST, "Missing method"), status_code=400)
    if method.startswith("notifications/"):
        return Response(status_code=202)

    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return error(request_id, INVALID_PARAMS, "params must be an object")
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    handler = methods.get(method)
    if handler is None:
        return error(request_id, METHOD_NOT_FOUND, f"Method '{method}' not found")
    try:
        answer = handler(params, request_id)
        return await answer if inspect.isawaitable(answer) else answer
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("MCP %s failed", method)
        return error(request_id, INTERNAL_ERROR, f"Internal error: {e}")


def arguments_of(params: Dict[str, Any]) -> Dict[str, Any]:
    """The arguments of tools/call or prompts/get; {} when absent or null."""
    return params.get("arguments") or {}


def wants_stream(request: Request) -> bool:
    """True for the GET by which an MCP client asks to open a stream, as opposed to a
    browser or curl asking for the server's information."""
    accept = request.headers.get("accept", "").lower()
    return "text/event-stream" in accept and "application/json" not in accept and "text/html" not in accept


def no_stream() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})
