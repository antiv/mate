#!/usr/bin/env python3
"""
Authenticated MATE (Multi-Agent Tree Engine) Server
Wraps the ADK web interface with basic HTTP authentication
"""

import os
import subprocess
import threading
import time
import httpx
import json
from typing import Optional, Dict, Any
from pathlib import Path

# Disable OpenTelemetry tracing to avoid TaskGroup errors with ParallelAgent
os.environ['OTEL_SDK_DISABLED'] = 'true'
from fastapi import FastAPI, HTTPException, Depends, status, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, RedirectResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.exception_handlers import http_exception_handler
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
import uvicorn
import websockets
import asyncio
from dotenv import load_dotenv
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from prometheus_fastapi_instrumentator import Instrumentator
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import secrets

# Load environment variables
load_dotenv()

# Security
security = HTTPBasic()
bearer_scheme = HTTPBearer()

# Use shared auth utilities for token management
from shared.utils.auth_utils import (
    generate_token, verify_token, revoke_token, active_tokens,
    logout_basic_auth, is_basic_auth_logged_out, clear_logged_out_status
)

# Configuration
from shared.utils.utils import get_adk_config, get_database_config

# Get configuration from shared utilities
adk_config = get_adk_config()
db_config = get_database_config()

ADK_HOST = adk_config["adk_host"]
ADK_PORT = adk_config["adk_port"]
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "mate")

# Database configuration
DB_TYPE = db_config["db_type"]
DB_PATH = db_config["db_path"]
DB_USER = db_config["db_user"]
DB_PASSWORD = db_config["db_password"]
DB_HOST = db_config["db_host"]
DB_PORT = db_config["db_port"]
DB_NAME = db_config["db_name"]

# Session service URI
SESSION_SERVICE_URI = adk_config["session_service_uri"]

# Define tag metadata for Swagger grouping
tags_metadata = [
    {
        "name": "System",
        "description": "System health and status endpoints",
    },
    {
        "name": "Authentication",
        "description": "Bearer token generation and management endpoints",
    },
    {
        "name": "MCP - Images",
        "description": "Image generation MCP server endpoints (DALL-E, Stable Diffusion, etc.)",
    },
    {
        "name": "MCP - Google Drive",
        "description": "Google Drive MCP server endpoints for file operations",
    },
    {
        "name": "Dashboard - Pages",
        "description": "Web interface pages for system management",
    },
    {
        "name": "Dashboard - Users",
        "description": "User management API endpoints",
    },
    {
        "name": "Dashboard - Agents",
        "description": "Agent configuration and management API endpoints",
    },
    {
        "name": "Dashboard - Migrations",
        "description": "Database migration management API endpoints",
    },
    {
        "name": "Dashboard - Server Control",
        "description": "ADK server control API endpoints (start, stop, restart)",
    },
    {
        "name": "Dashboard - Usage Analytics",
        "description": "Token usage and analytics API endpoints",
    },
    {
        "name": "Proxy - ADK Web",
        "description": "Proxies to the main ADK web interface",
    },
    {
        "name": "Proxy - ADK Documentation",
        "description": "Proxies to ADK API documentation (Swagger, ReDoc, OpenAPI schema)",
    },
    {
        "name": "Proxy - ADK API",
        "description": "Generic proxy for all ADK API endpoints with streaming support",
    },
    {
        "name": "Examples",
        "description": "Example endpoints demonstrating authentication patterns",
    },
]

# Create FastAPI app
app = FastAPI(
    title="MATE - Authenticated", 
    version="1.0.0", 
    description="Authentication layer for MATE (Multi-Agent Tree Engine) with admin management endpoints",
    docs_url=None,  # Disabled - /docs proxies to ADK
    redoc_url=None,  # Disabled - /redoc proxies to ADK
    openapi_url=None,  # Disabled - custom admin-openapi.json endpoint used instead
    openapi_tags=tags_metadata
)

# Get project root
project_root = Path(__file__).parent

# MCP Server Integration
image_mcp_server = None
gdrive_mcp_server = None
agent_mcp_manager = None

# Dashboard Server Integration
dashboard_server = None

def initialize_mcp_servers():
    """Initialize MCP servers."""
    global image_mcp_server, gdrive_mcp_server, agent_mcp_manager
    
    try:
        # Initialize Image MCP Server
        from shared.utils.mcp.image_mcp_server import ImageMCPServer
        image_mcp_server = ImageMCPServer(app, True)
        image_mcp_server.check_image_mcp_availability()
        
        # Initialize Google Drive MCP Server
        from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer
        gdrive_mcp_server = GoogleDriveMCPServer(app, True)
        gdrive_mcp_server.check_gdrive_mcp_availability()
        
        # Initialize Agent MCP Manager (dynamic agent MCP servers)
        from shared.utils.mcp.agent_mcp_manager import AgentMCPManager
        agent_mcp_manager = AgentMCPManager(app)
        agent_mcp_manager.initialize_agent_mcp_servers()
            
    except Exception as e:
        print(f"⚠️  MCP servers initialization error: {e}")
        import traceback
        traceback.print_exc()

def initialize_dashboard_server():
    """Initialize Dashboard server."""
    global dashboard_server
    
    try:
        # Initialize Dashboard Server
        from shared.utils.dashboard.dashboard_server import DashboardServer
        dashboard_server = DashboardServer(app, project_root)
        print("✅ Dashboard server initialized successfully")
        
    except Exception as e:
        print(f"⚠️  Dashboard server initialization error: {e}")

def initialize_agent_folders():
    """Create agent folders for all top-level agents (without parent agents) from template."""
    try:
        # Use ServerControlService to initialize agent folders
        # This keeps the logic in one place and ensures consistency
        server_control = ServerControlService(
            adk_host=ADK_HOST,
            adk_port=ADK_PORT,
            session_service_uri=SESSION_SERVICE_URI
        )
        server_control._initialize_agent_folders()
    except Exception as e:
        print(f"⚠️  Agent folder initialization error: {e}")

# Initialize Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import shared server control service (needed by initialize_agent_folders)
from shared.utils.server_control_service import ServerControlService

# Initialize servers after app setup
initialize_mcp_servers()
initialize_dashboard_server()

# Custom exception handler for 401 errors to ensure proper WWW-Authenticate header
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and "WWW-Authenticate" in exc.headers:
        # Create a proper response with WWW-Authenticate header
        return Response(
            content='{"detail":"' + exc.detail + '"}',
            status_code=401,
            headers={"WWW-Authenticate": exc.headers["WWW-Authenticate"]},
            media_type="application/json"
        )
    return await http_exception_handler(request, exc)

# Authentication functions
def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify basic auth credentials
    
    Note: This does NOT check for logged-out status because it's used for explicit login attempts.
    Logged-out status only prevents automatic browser-sent credentials in get_auth_user/get_dashboard_auth_user.
    When user successfully authenticates via /auth/token, logged-out status is cleared."""
    if credentials.username == AUTH_USERNAME and credentials.password == AUTH_PASSWORD:
        return credentials
    raise HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Basic"},
    )

def verify_bearer_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """Verify bearer token"""
    if not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials

def get_auth_user(request: Request):
    """Get authenticated user from either Bearer token or Basic auth
    This is used for ADK proxy routes and triggers browser popup for Basic auth"""
    auth_header = request.headers.get("Authorization", "")
    
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
        if verify_token(token):
            return AUTH_USERNAME
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    elif auth_header.startswith("Basic "):
        # Parse basic auth
        try:
            import base64
            encoded_credentials = auth_header[6:]  # Remove "Basic " prefix
            decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
            username, password = decoded_credentials.split(':', 1)
            
            # Check if credentials are logged out
            if is_basic_auth_logged_out(username, password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session logged out. Please login again.",
                    headers={"WWW-Authenticate": "Basic realm=\"MATE\""},
                )
            
            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                return username
        except HTTPException:
            raise
        except Exception:
            pass
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic realm=\"MATE\""},
        )
    else:
        # Raise HTTPException with proper headers to trigger browser auth dialog
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Basic realm=\"MATE\""},
        )

def get_dashboard_auth_user(request: Request):
    """Get authenticated user for dashboard routes without triggering browser popup.
    Returns username if authenticated, or None if not authenticated.
    Checks Authorization header first, then falls back to auth_token cookie."""
    # Check Authorization header first
    auth_header = request.headers.get("Authorization", "")
    
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
        is_valid = verify_token(token)
        if not is_valid:
            print(f"🔍 [DASHBOARD AUTH] Bearer token invalid: {token[:20]}...")
        if is_valid:
            return AUTH_USERNAME
        return None
    elif auth_header.startswith("Basic "):
        # Parse basic auth
        try:
            import base64
            encoded_credentials = auth_header[6:]  # Remove "Basic " prefix
            decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
            username, password = decoded_credentials.split(':', 1)
            
            # Check if credentials are logged out
            if is_basic_auth_logged_out(username, password):
                print(f"🔍 [DASHBOARD AUTH] Basic auth logged out for: {username}")
                return None
            
            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                return username
        except Exception:
            pass
    
    # If no Authorization header, check for auth_token cookie
    cookies = request.cookies
    token = cookies.get("auth_token")
    if token:
        is_valid = verify_token(token)
        if not is_valid:
            print(f"🔍 [DASHBOARD AUTH] Cookie token invalid: {token[:20]}...")
        if is_valid:
            return AUTH_USERNAME
    
    return None

def require_dashboard_auth(request: Request):
    """Require authentication for dashboard routes. Redirects to login if not authenticated."""
    username = get_dashboard_auth_user(request)
    if username is None:
        # Redirect to login with redirect parameter
        redirect_url = str(request.url.path)
        if request.url.query:
            redirect_url += "?" + request.url.query
        # URL encode the redirect parameter
        from urllib.parse import quote
        encoded_redirect = quote(redirect_url, safe='')
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/login?redirect={encoded_redirect}"}
        )
    return username

# Login page endpoint
@app.get("/login", tags=["Dashboard - Pages"])
async def login_page(request: Request):
    """Login page for dashboard (no auth required)"""
    from fastapi.templating import Jinja2Templates
    templates_dir = project_root / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    return templates.TemplateResponse("login.html", {"request": request})

# Handle Chrome DevTools requests (harmless, just suppress 404 logs)
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools():
    """Handle Chrome DevTools configuration requests"""
    return Response(status_code=404)

# Root endpoint - proxy to ADK web server
@app.get("/", tags=["Proxy - ADK Web"])
async def root(request: Request, username: str = Depends(get_auth_user)):
    """Root endpoint - proxies to ADK web server"""
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            
            # Return the response from ADK server
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers)
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}"
        )

# Health check endpoint
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint (no auth required)"""
    image_status = "available" if image_mcp_server and image_mcp_server.image_mcp_available else "unavailable"
    gdrive_status = "available" if gdrive_mcp_server and gdrive_mcp_server.gdrive_mcp_available else "unavailable"
    dashboard_status = "available" if dashboard_server else "unavailable"
    return {
        "status": "healthy", 
        "service": "mate-auth",
        "image_mcp": image_status,
        "gdrive_mcp": gdrive_status,
        "dashboard": dashboard_status
    }

# Authentication endpoints
@app.post("/auth/token", tags=["Authentication"])
async def generate_auth_token(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Generate a Bearer token for authenticated users"""
    # Clear logged-out status when user successfully authenticates
    # This allows login after logout without waiting for expiry
    clear_logged_out_status(credentials.username, credentials.password)
    
    token = generate_token()
    print(f"🔍 [TOKEN DEBUG] Token generated in /auth/token: {token[:30]}..., active_tokens count: {len(active_tokens)}")
    return {"access_token": token, "token_type": "bearer", "username": credentials.username}

@app.post("/auth/revoke", tags=["Authentication"])
async def revoke_auth_token(token: str, credentials: HTTPAuthorizationCredentials = Depends(verify_bearer_token)):
    """Revoke a Bearer token"""
    revoke_token(token)
    return {"message": "Token revoked successfully"}

@app.delete("/auth/token", tags=["Authentication"])
async def revoke_auth_token_delete(request: Request, username: str = Depends(get_auth_user)):
    """Revoke the current Bearer token"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        revoke_token(token)
        return {"message": "Token revoked successfully"}
    return {"message": "No token to revoke"}

@app.post("/auth/logout", tags=["Authentication"])
async def logout(request: Request):
    """Logout endpoint - revokes token and invalidates basic auth (no auth required)"""
    username = None
    password = None
    
    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        print(f"🔍 [LOGOUT] Revoking Bearer token: {token[:20]}...")
        revoke_token(token)
    elif auth_header.startswith("Basic "):
        # Extract credentials to mark as logged out
        try:
            import base64
            encoded_credentials = auth_header[6:]  # Remove "Basic " prefix
            decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
            username, password = decoded_credentials.split(':', 1)
            # Mark basic auth as logged out
            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                print(f"🔍 [LOGOUT] Logging out Basic auth for: {username}")
                logout_basic_auth(username, password)
        except Exception as e:
            print(f"🔍 [LOGOUT] Error parsing Basic auth: {e}")
    
    # Revoke all tokens from cookies
    cookies = request.cookies
    token = cookies.get("auth_token")
    if token:
        print(f"🔍 [LOGOUT] Revoking cookie token: {token[:20]}...")
        revoke_token(token)
    
    # Get credentials from request body if available (client sends in JSON)
    try:
        body = await request.json()
        if not username and "username" in body and "password" in body:
            username = body["username"]
            password = body["password"]
            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                print(f"🔍 [LOGOUT] Logging out Basic auth from body: {username}")
                logout_basic_auth(username, password)
    except Exception:
        pass
    
    # If we still don't have credentials but have a token, mark all configured credentials as logged out
    # This is a fallback to ensure logout works even if credentials aren't available
    if not username:
        # Check if any token was revoked (meaning user was logged in)
        # If so, mark credentials as logged out as a safety measure
        # Note: This is a temporary measure - ideally credentials should come from the request
        if token or auth_header.startswith("Bearer "):
            print(f"🔍 [LOGOUT] Fallback: Marking configured credentials as logged out")
            logout_basic_auth(AUTH_USERNAME, AUTH_PASSWORD)
    
    # Return response with cookie deletion headers
    # Use multiple path variations to ensure cookie is cleared
    response = JSONResponse({"message": "Logged out successfully"})
    response.set_cookie("auth_token", "", max_age=0, path="/", httponly=False, samesite="lax")
    response.set_cookie("auth_token", "", max_age=0, path="/dashboard", httponly=False, samesite="lax")
    response.set_cookie("auth_username", "", max_age=0, path="/", httponly=False, samesite="lax")
    response.set_cookie("auth_password", "", max_age=0, path="/", httponly=False, samesite="lax")
    return response

@app.get("/protected", tags=["Examples"])
async def protected_endpoint(request: Request, username: str = Depends(get_auth_user)):
    """Example protected endpoint"""
    return {"message": "Access granted", "username": username}

# Proxy endpoints for ADK server
@app.get("/docs", tags=["Proxy - ADK Documentation"])
async def proxy_adk_docs(request: Request, username: str = Depends(get_auth_user)):
    """Proxy ADK swagger documentation"""
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/docs"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type")
            )
    except Exception as e:
            raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}"
        )

@app.get("/redoc", tags=["Proxy - ADK Documentation"])
async def proxy_adk_redoc(request: Request, username: str = Depends(get_auth_user)):
    """Proxy ADK redoc documentation"""
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/redoc"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(target_url, timeout=30.0)
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type")
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}"
        )

@app.get("/openapi.json", tags=["Proxy - ADK Documentation"])
async def serve_openapi_schema():
    """Serve OpenAPI schema from static folder (ADK API)"""
    openapi_path = project_root / "static" / "openapi.json"
    
    if not openapi_path.exists():
        raise HTTPException(
            status_code=404,
            detail="OpenAPI schema file not found"
        )
    
    return FileResponse(
        path=openapi_path,
        media_type="application/json",
        headers={"Cache-Control": "no-cache"}
    )

# Admin documentation endpoints (for auth_server API)
@app.get("/admin-openapi.json", include_in_schema=False)
async def get_admin_openapi_schema(username: str = Depends(get_auth_user)):
    """Get OpenAPI schema for admin/auth server endpoints"""
    return JSONResponse(app.openapi())

@app.get("/admin-docs", include_in_schema=False)
async def get_admin_documentation(username: str = Depends(get_auth_user)):
    """Swagger UI documentation for admin/auth server endpoints"""
    return get_swagger_ui_html(
        openapi_url="/admin-openapi.json",
        title=f"{app.title} - Admin API Documentation",
        swagger_favicon_url="/static/favicon.svg",
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True, "filter": True}
    )

@app.get("/admin-redoc", include_in_schema=False)
async def get_admin_redoc(username: str = Depends(get_auth_user)):
    """ReDoc documentation for admin/auth server endpoints"""
    return get_redoc_html(
        openapi_url="/admin-openapi.json",
        title=f"{app.title} - Admin API Documentation",
        redoc_favicon_url="/static/favicon.svg"
    )

# WebSocket proxy endpoint for /run_live
@app.websocket("/run_live")
async def websocket_run_live(
    websocket: WebSocket,
    app_name: str = Query(...),
    user_id: str = Query(...),
    session_id: str = Query(...),
    token: Optional[str] = Query(None)
):
    """Proxy WebSocket connection to ADK server's /run_live endpoint"""
    # Verify authentication via token in query param or headers
    authenticated = False
    
    # Check token in query params
    if token and verify_token(token):
        authenticated = True
    
    # Check Authorization header
    if not authenticated:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token_from_header = auth_header[7:]
            if verify_token(token_from_header):
                authenticated = True
        elif auth_header.startswith("Basic "):
            try:
                import base64
                encoded_credentials = auth_header[6:]
                decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
                username, password = decoded_credentials.split(':', 1)
                if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                    authenticated = True
            except Exception:
                pass
    
    if not authenticated:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    # Connect to the backend ADK WebSocket
    backend_ws_url = f"ws://{ADK_HOST}:{ADK_PORT}/run_live?app_name={app_name}&user_id={user_id}&session_id={session_id}"
    
    try:
        await websocket.accept()
        
        async with websockets.connect(backend_ws_url) as backend_ws:
            # Create tasks for bidirectional proxying
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
            
            # Run both directions concurrently
            await asyncio.gather(
                forward_to_backend(),
                forward_to_client(),
                return_exceptions=True
            )
    except Exception as e:
        print(f"WebSocket proxy error: {e}")
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except:
            pass

# Generic proxy endpoint for all other ADK routes
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"], tags=["Proxy - ADK API"])
async def proxy_adk(request: Request, path: str, username: str = Depends(get_auth_user)):
    """Proxy all requests to the ADK server, with support for streaming SSE."""
    # Skip dashboard routes - they should be handled by dashboard endpoints
    if path.startswith("dashboard/"):
        raise HTTPException(status_code=404, detail="Not found")
    
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/{path}"
    
    # Get request body for relevant methods
    body = await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
    
    # Copy headers, excluding 'host' as it's managed by httpx
    headers = {key: value for key, value in request.headers.items() if key.lower() != 'host'}

    client = httpx.AsyncClient(timeout=900.0)
    try:
        # For SSE endpoints, we need to handle streaming differently
        if path == "run_sse" or request.headers.get("accept") == "text/event-stream":
            # Send request and stream the response
            req = client.build_request(
                method=request.method,
                url=target_url,
                params=request.query_params,
                headers=headers,
                content=body
            )
            
            r = await client.send(req, stream=True)
            
            # Stream the response directly
            async def streamer():
                try:
                    async for chunk in r.aiter_bytes():
                        yield chunk
                finally:
                    await r.aclose()
                    await client.aclose()
            
            # Return streaming response with proper headers
            response_headers = dict(r.headers)
            return StreamingResponse(
                streamer(), 
                status_code=r.status_code, 
                headers=response_headers,
                media_type=r.headers.get('content-type', 'text/event-stream')
            )
        
        else:
            # For non-streaming requests, handle normally
            req = client.build_request(
                method=request.method,
                url=target_url,
                params=request.query_params,
                headers=headers,
                content=body
            )
            
            r = await client.send(req, stream=True)
            
            # Check if response is actually streaming based on content-type
            if 'text/event-stream' in r.headers.get('content-type', ''):
                # If it's a stream, return a StreamingResponse that yields chunks
                async def streamer():
                    try:
                        async for chunk in r.aiter_bytes():
                            yield chunk
                    finally:
                        await r.aclose()
                        await client.aclose()
                
                # Pass the original headers from the upstream response
                return StreamingResponse(streamer(), status_code=r.status_code, headers=r.headers)
            else:
                # If it's not a stream, read the entire response and return it at once
                await r.aread()
                await client.aclose()
                return Response(content=r.content, status_code=r.status_code, headers=r.headers)

    except httpx.RequestError as e:
        # Ensure client is closed on error
        await client.aclose()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ADK server is not available: {str(e)}"
        )

if __name__ == "__main__":
    # Initialize server control service with proper configuration
    server_control = ServerControlService(
        adk_host=ADK_HOST,
        adk_port=ADK_PORT,
        session_service_uri=SESSION_SERVICE_URI
    )
    
    # Start ADK server in background thread using the class method
    def start_adk_in_thread():
        result = server_control.start_adk_server()
        if not result.get("success", True):
            print(f"ADK server startup: {result.get('message', 'Unknown error')}")
    
    adk_thread = threading.Thread(target=start_adk_in_thread, daemon=True)
    adk_thread.start()
    
    # Wait a moment for ADK server to start
    time.sleep(3)
    
    # Start the authenticated server
    print(f"Starting authenticated server on port 8000")
    print(f"ADK server will be available on port {ADK_PORT}")
    print(f"Username: {AUTH_USERNAME}")
    print(f"Password: {'*' * len(AUTH_PASSWORD)}")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
