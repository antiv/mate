"""
ADK proxy routes for MATE auth server.

Handles proxying requests to the ADK backend server, including:
- Root page proxy
- Documentation proxy (Swagger, ReDoc, OpenAPI)
- Admin documentation endpoints
- WebSocket proxy for /run_live
- Generic catch-all proxy for all ADK API routes
"""

import logging
import asyncio
from typing import Optional, Dict, Any
from pathlib import Path

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import StreamingResponse, Response, FileResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from shared.utils.auth_utils import verify_token
from server.auth import get_auth_user, AUTH_USERNAME, AUTH_PASSWORD

logger = logging.getLogger(__name__)


def _inject_trace_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Inject W3C trace context into headers for propagation to ADK."""
    try:
        from shared.utils.tracing.tracing_config import is_tracing_enabled
        if not is_tracing_enabled():
            return headers
        from opentelemetry import trace
        from opentelemetry.propagate import inject
        carrier: Dict[str, str] = dict(headers)
        inject(carrier)
        return carrier
    except Exception:
        return headers

router = APIRouter()

project_root = Path(__file__).parent.parent

ADK_HOST: str = "localhost"
ADK_PORT: int = 8001


def configure_proxy(adk_host: str, adk_port: int):
    """Configure the proxy target."""
    global ADK_HOST, ADK_PORT
    ADK_HOST = adk_host
    ADK_PORT = adk_port


@router.get("/", tags=["Proxy - ADK Web"])
async def root(request: Request, username: str = Depends(get_auth_user)):
    """Root endpoint - proxies to ADK web server."""
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}",
        )


@router.get("/docs", tags=["Proxy - ADK Documentation"])
async def proxy_adk_docs(request: Request, username: str = Depends(get_auth_user)):
    """Proxy ADK swagger documentation."""
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/docs"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type"),
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}",
        )


@router.get("/redoc", tags=["Proxy - ADK Documentation"])
async def proxy_adk_redoc(request: Request, username: str = Depends(get_auth_user)):
    """Proxy ADK redoc documentation."""
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/redoc"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type"),
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}",
        )


@router.get("/openapi.json", tags=["Proxy - ADK Documentation"])
async def serve_openapi_schema():
    """Serve OpenAPI schema from static folder (ADK API)."""
    openapi_path = project_root / "static" / "openapi.json"
    if not openapi_path.exists():
        raise HTTPException(status_code=404, detail="OpenAPI schema file not found")
    return FileResponse(
        path=openapi_path,
        media_type="application/json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/protected", tags=["Examples"])
async def protected_endpoint(request: Request, username: str = Depends(get_auth_user)):
    """Example protected endpoint."""
    return {"message": "Access granted", "username": username}


@router.websocket("/run_live")
async def websocket_run_live(
    websocket: WebSocket,
    app_name: str = Query(...),
    user_id: str = Query(...),
    session_id: str = Query(...),
    token: Optional[str] = Query(None),
):
    """Proxy WebSocket connection to ADK server's /run_live endpoint."""
    authenticated = False

    if token and verify_token(token):
        authenticated = True

    if not authenticated:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            if verify_token(auth_header[7:]):
                authenticated = True
        elif auth_header.startswith("Basic "):
            try:
                import base64
                encoded_credentials = auth_header[6:]
                decoded_credentials = base64.b64decode(encoded_credentials).decode("utf-8")
                username, password = decoded_credentials.split(":", 1)
                if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                    authenticated = True
            except Exception:
                pass

    if not authenticated:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    backend_ws_url = f"ws://{ADK_HOST}:{ADK_PORT}/run_live?app_name={app_name}&user_id={user_id}&session_id={session_id}"

    try:
        await websocket.accept()

        async with websockets.connect(backend_ws_url) as backend_ws:

            async def forward_to_backend():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await backend_ws.send(data)
                except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
                    pass

            async def forward_to_client():
                try:
                    async for message in backend_ws:
                        await websocket.send_text(message)
                except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
                    pass

            await asyncio.gather(
                forward_to_backend(),
                forward_to_client(),
                return_exceptions=True,
            )
    except Exception as e:
        logger.error("WebSocket proxy error: %s", e)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["Proxy - ADK API"],
)
async def proxy_adk(request: Request, path: str, username: str = Depends(get_auth_user)):
    """Proxy all requests to the ADK server, with support for streaming SSE."""
    if path.startswith("dashboard/"):
        raise HTTPException(status_code=404, detail="Not found")

    target_url = f"http://{ADK_HOST}:{ADK_PORT}/{path}"
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    headers = {key: value for key, value in request.headers.items() if key.lower() != "host"}
    headers = _inject_trace_headers(headers)

    client = httpx.AsyncClient(timeout=900.0)
    try:
        if path == "run_sse" or request.headers.get("accept") == "text/event-stream":
            req = client.build_request(
                method=request.method,
                url=target_url,
                params=request.query_params,
                headers=headers,
                content=body,
            )
            r = await client.send(req, stream=True)

            async def streamer():
                try:
                    async for chunk in r.aiter_bytes():
                        yield chunk
                finally:
                    await r.aclose()
                    await client.aclose()

            response_headers = dict(r.headers)
            return StreamingResponse(
                streamer(),
                status_code=r.status_code,
                headers=response_headers,
                media_type=r.headers.get("content-type", "text/event-stream"),
            )
        else:
            req = client.build_request(
                method=request.method,
                url=target_url,
                params=request.query_params,
                headers=headers,
                content=body,
            )
            r = await client.send(req, stream=True)

            if "text/event-stream" in r.headers.get("content-type", ""):

                async def streamer():
                    try:
                        async for chunk in r.aiter_bytes():
                            yield chunk
                    finally:
                        await r.aclose()
                        await client.aclose()

                return StreamingResponse(streamer(), status_code=r.status_code, headers=r.headers)
            else:
                await r.aread()
                await client.aclose()
                return Response(content=r.content, status_code=r.status_code, headers=r.headers)

    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}",
        )
