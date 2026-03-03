#!/usr/bin/env python3
"""
Authenticated MATE (Multi-Agent Tree Engine) Server
Wraps the ADK web interface with basic HTTP authentication
"""

import os
import logging
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from shared.utils.logging_config import configure_logging
configure_logging()

# Disable OpenTelemetry tracing to avoid TaskGroup errors with ParallelAgent
# Only when OTEL_TRACING_ENABLED is not explicitly enabled
if os.getenv("OTEL_TRACING_ENABLED", "false").lower() not in ("true", "1", "yes"):
    os.environ["OTEL_SDK_DISABLED"] = "true"

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exception_handlers import http_exception_handler
from fastapi import HTTPException
import uvicorn
from dotenv import load_dotenv
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)

# Configuration
from shared.utils.utils import get_adk_config, get_database_config

adk_config = get_adk_config()
db_config = get_database_config()

ADK_HOST = adk_config["adk_host"]
ADK_PORT = adk_config["adk_port"]
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "mate")
SESSION_SERVICE_URI = adk_config["session_service_uri"]

if AUTH_PASSWORD == "mate":
    logger.warning("Using default AUTH_PASSWORD. Set AUTH_PASSWORD env var for production use.")

# Database configuration
DB_TYPE = db_config["db_type"]
DB_PATH = db_config["db_path"]
DB_USER = db_config["db_user"]
DB_PASSWORD = db_config["db_password"]
DB_HOST = db_config["db_host"]
DB_PORT = db_config["db_port"]
DB_NAME = db_config["db_name"]

# Configure auth and proxy modules
from server.auth import configure_auth
from server.proxy_routes import configure_proxy

configure_auth(AUTH_USERNAME, AUTH_PASSWORD)
configure_proxy(ADK_HOST, ADK_PORT)

# Tag metadata for Swagger grouping
tags_metadata = [
    {"name": "System", "description": "System health and status endpoints"},
    {"name": "Authentication", "description": "Bearer token generation and management endpoints"},
    {"name": "MCP - Images", "description": "Image generation MCP server endpoints (DALL-E, Stable Diffusion, etc.)"},
    {"name": "MCP - Google Drive", "description": "Google Drive MCP server endpoints for file operations"},
    {"name": "Dashboard - Pages", "description": "Web interface pages for system management"},
    {"name": "Dashboard - Users", "description": "User management API endpoints"},
    {"name": "Dashboard - Agents", "description": "Agent configuration and management API endpoints"},
    {"name": "Dashboard - Migrations", "description": "Database migration management API endpoints"},
    {"name": "Dashboard - Server Control", "description": "ADK server control API endpoints (start, stop, restart)"},
    {"name": "Dashboard - Usage Analytics", "description": "Token usage and analytics API endpoints"},
    {"name": "Proxy - ADK Web", "description": "Proxies to the main ADK web interface"},
    {"name": "Proxy - ADK Documentation", "description": "Proxies to ADK API documentation (Swagger, ReDoc, OpenAPI schema)"},
    {"name": "Proxy - ADK API", "description": "Generic proxy for all ADK API endpoints with streaming support"},
    {"name": "Widget", "description": "Embeddable chat widget endpoints (public, authenticated via widget API key)"},
    {"name": "Widget - Admin API", "description": "Widget admin API for agent, memory blocks, and file management"},
    {"name": "Dashboard - Widget Keys", "description": "Widget API key management endpoints"},
    {"name": "Examples", "description": "Example endpoints demonstrating authentication patterns"},
]

# Create FastAPI app
app = FastAPI(
    title="MATE - Authenticated",
    version="1.0.0",
    description="Authentication layer for MATE (Multi-Agent Tree Engine) with admin management endpoints",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    openapi_tags=tags_metadata,
)

project_root = Path(__file__).parent

# ---------- MCP Server Integration ----------
image_mcp_server = None
gdrive_mcp_server = None
agent_mcp_manager = None
dashboard_server = None


def initialize_mcp_servers():
    global image_mcp_server, gdrive_mcp_server, agent_mcp_manager
    try:
        from shared.utils.mcp.image_mcp_server import ImageMCPServer
        image_mcp_server = ImageMCPServer(app, True)
        image_mcp_server.check_image_mcp_availability()

        from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer
        gdrive_mcp_server = GoogleDriveMCPServer(app, True)
        gdrive_mcp_server.check_gdrive_mcp_availability()

        from shared.utils.mcp.agent_mcp_manager import AgentMCPManager
        agent_mcp_manager = AgentMCPManager(app)
        agent_mcp_manager.initialize_agent_mcp_servers()
    except Exception as e:
        logger.warning("MCP servers initialization error: %s", e, exc_info=True)


def initialize_dashboard_server():
    global dashboard_server
    try:
        from shared.utils.dashboard.dashboard_server import DashboardServer
        dashboard_server = DashboardServer(app, project_root)
        logger.info("Dashboard server initialized successfully")
    except Exception as e:
        logger.warning("Dashboard server initialization error: %s", e)


def initialize_agent_folders():
    try:
        server_control = ServerControlService(
            adk_host=ADK_HOST,
            adk_port=ADK_PORT,
            session_service_uri=SESSION_SERVICE_URI,
        )
        server_control._initialize_agent_folders()
    except Exception as e:
        logger.warning("Agent folder initialization error: %s", e)


# ---------- Middleware and Instrumentation ----------
Instrumentator().instrument(app).expose(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from shared.utils.server_control_service import ServerControlService

# Initialize servers after app setup
initialize_mcp_servers()
initialize_dashboard_server()


# ---------- Custom exception handler ----------
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and exc.headers and "WWW-Authenticate" in exc.headers:
        return Response(
            content='{"detail":"' + exc.detail + '"}',
            status_code=401,
            headers={"WWW-Authenticate": exc.headers["WWW-Authenticate"]},
            media_type="application/json",
        )
    return await http_exception_handler(request, exc)


# Handle Chrome DevTools requests
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools():
    return Response(status_code=404)


# ---------- Health check ----------
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint (no auth required)."""
    image_status = "available" if image_mcp_server and image_mcp_server.image_mcp_available else "unavailable"
    gdrive_status = "available" if gdrive_mcp_server and gdrive_mcp_server.gdrive_mcp_available else "unavailable"
    dashboard_status = "available" if dashboard_server else "unavailable"
    return {
        "status": "healthy",
        "service": "mate-auth",
        "image_mcp": image_status,
        "gdrive_mcp": gdrive_status,
        "dashboard": dashboard_status,
    }


# ---------- Admin documentation ----------
from server.auth import get_auth_user
from fastapi import Depends
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html


@app.get("/admin-openapi.json", include_in_schema=False)
async def get_admin_openapi_schema(username: str = Depends(get_auth_user)):
    return JSONResponse(app.openapi())


@app.get("/admin-docs", include_in_schema=False)
async def get_admin_documentation(username: str = Depends(get_auth_user)):
    return get_swagger_ui_html(
        openapi_url="/admin-openapi.json",
        title=f"{app.title} - Admin API Documentation",
        swagger_favicon_url="/static/favicon.svg",
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True, "filter": True},
    )


@app.get("/admin-redoc", include_in_schema=False)
async def get_admin_redoc(username: str = Depends(get_auth_user)):
    return get_redoc_html(
        openapi_url="/admin-openapi.json",
        title=f"{app.title} - Admin API Documentation",
        redoc_favicon_url="/static/favicon.svg",
    )


# ---------- Include routers ----------
from server.auth_routes import router as auth_router
from server.proxy_routes import router as proxy_router
from server.widget_routes import (
    router as widget_router,
    admin_api_router as widget_admin_api_router,
    dashboard_widget_router,
    configure_widget_proxy,
)

configure_widget_proxy(ADK_HOST, ADK_PORT)

app.include_router(auth_router)
app.include_router(widget_router)
app.include_router(widget_admin_api_router)
app.include_router(dashboard_widget_router)
app.include_router(proxy_router)


# ---------- Entry point ----------
if __name__ == "__main__":
    server_control = ServerControlService(
        adk_host=ADK_HOST,
        adk_port=ADK_PORT,
        session_service_uri=SESSION_SERVICE_URI,
    )

    def start_adk_in_thread():
        result = server_control.start_adk_server()
        if not result.get("success", True):
            logger.warning("ADK server startup: %s", result.get("message", "Unknown error"))

    adk_thread = threading.Thread(target=start_adk_in_thread, daemon=True)
    adk_thread.start()

    time.sleep(3)

    logger.info("Starting authenticated server on port 8000")
    logger.info("ADK server will be available on port %s", ADK_PORT)
    logger.info("Username: %s", AUTH_USERNAME)

    uvicorn.run(app, host="0.0.0.0", port=8000)
