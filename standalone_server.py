#!/usr/bin/env python3
"""
MATE Standalone Server — minimal single-agent runtime for packaged builds.

This replaces auth_server.py + adk_main.py for standalone distributions.
It serves a single agent's chat UI with no dashboard, no auth, no widget keys.

Usage:
    python standalone_server.py [--port 8080] [--no-browser]

Environment:
    ROOT_AGENT_NAME  — name of the agent to serve (required)
    DB_TYPE          — database type (default: sqlite)
    DB_PATH          — path to SQLite database (default: standalone_agent.db)
    GOOGLE_API_KEY   — Gemini API key (if using Gemini models)
    OPENROUTER_API_KEY — OpenRouter API key (if using OpenRouter models)
"""

import os
import sys
import shutil


def _enrich_path():
    """
    Prepend common Node.js / Python tool directories to PATH so that MCP stdio
    servers (e.g. `npx`, `uvx`) can be found when the binary is launched from
    a GUI context (macOS Finder, Windows Explorer) where the shell PATH is minimal.
    """
    extra_paths = []
    home = os.path.expanduser("~")

    if sys.platform == "darwin":
        import glob
        extra_paths = [
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            f"{home}/.fnm/current/bin",
            f"{home}/.nodenv/shims",
            f"{home}/.volta/bin",
            f"{home}/.bun/bin",
            f"{home}/.local/bin",
        ]
        nvm_dirs = glob.glob(f"{home}/.nvm/versions/node/*/bin")
        if nvm_dirs:
            nvm_dirs.sort(reverse=True)
            extra_paths.extend(nvm_dirs)

    elif sys.platform == "linux":
        import glob
        extra_paths = [
            "/usr/local/bin",
            "/home/linuxbrew/.linuxbrew/bin",
            f"{home}/.local/bin",
            f"{home}/.fnm/current/bin",
            f"{home}/.nodenv/shims",
            f"{home}/.volta/bin",
            f"{home}/.bun/bin",
        ]
        nvm_dirs = glob.glob(f"{home}/.nvm/versions/node/*/bin")
        if nvm_dirs:
            nvm_dirs.sort(reverse=True)
            extra_paths.extend(nvm_dirs)

    if extra_paths:
        current_path = os.environ.get("PATH", "")
        existing_parts = current_path.split(os.pathsep) if current_path else []
        for ep in extra_paths:
            if ep not in existing_parts:
                current_path = ep + os.pathsep + current_path
        os.environ["PATH"] = current_path


_enrich_path()

import webbrowser
import threading
import time
import argparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Determine the base directory (works for both script and PyInstaller)
if getattr(sys, 'frozen', False):
    # Running as a PyInstaller bundle
    BASE_DIR = Path(sys._MEIPASS)
    # Also look for .env next to the executable
    EXEC_DIR = Path(sys.executable).parent
    if (EXEC_DIR / ".env").exists():
        load_dotenv(EXEC_DIR / ".env")
    elif (BASE_DIR / ".env").exists():
        load_dotenv(BASE_DIR / ".env")
else:
    BASE_DIR = Path(__file__).parent
    load_dotenv(BASE_DIR / ".env")

# Ensure shared modules are importable
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Disable OpenTelemetry tracing (avoids missing dependency errors in packaged builds)
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from shared.utils.logging_config import configure_logging
configure_logging()


import logging
import json
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ROOT_AGENT_NAME = os.getenv("ROOT_AGENT_NAME", "")
if not ROOT_AGENT_NAME:
    print("❌ ROOT_AGENT_NAME environment variable is required.")
    print("   Set it in your .env file or environment before running.")
    sys.exit(1)

PORT = int(os.getenv("STANDALONE_PORT", "8080"))
HOST = os.getenv("STANDALONE_HOST", "127.0.0.1")

print(f"🚀 MATE Standalone Server")
print(f"   Agent: {ROOT_AGENT_NAME}")
print(f"   Database: {os.getenv('DB_TYPE', 'sqlite')} / {os.getenv('DB_PATH', 'standalone_agent.db')}")


def _print_mcp_command_status():
    """
    Scan all agents in the database for MCP server configs and print which
    external commands (e.g. npx) are available vs missing at startup.
    This gives users immediate actionable feedback before any prompt fails.
    """
    try:
        import json as _json
        from shared.utils.database_client import get_database_client
        from shared.utils.models import AgentConfig

        db = get_database_client()
        if not db.is_connected():
            return
        session = db.get_session()
        if not session:
            return
        try:
            agents = session.query(AgentConfig).filter(AgentConfig.disabled == False).all()
            commands_seen: set = set()
            any_mcp = False

            for agent in agents:
                raw = agent.mcp_servers_config
                if not raw:
                    continue
                try:
                    cfg = _json.loads(raw) if isinstance(raw, str) else raw
                    servers = cfg.get("mcpServers", {})
                    for srv_name, srv_cfg in servers.items():
                        cmd = srv_cfg.get("command", "").strip()
                        if not cmd or cmd in commands_seen:
                            continue
                        commands_seen.add(cmd)
                        any_mcp = True
                        resolved = shutil.which(cmd)
                        if resolved:
                            print(f"   🔌 MCP '{cmd}': ✅ {resolved}")
                        else:
                            hints = {
                                "npx": "Install Node.js from https://nodejs.org/",
                                "node": "Install Node.js from https://nodejs.org/",
                                "uvx": "Install uv from https://github.com/astral-sh/uv",
                                "bun": "Install Bun from https://bun.sh/",
                                "deno": "Install Deno from https://deno.land/",
                            }
                            hint = hints.get(cmd, f"Ensure '{cmd}' is installed and in PATH")
                            print(f"   🔌 MCP '{cmd}': ❌ NOT FOUND — {hint}")
                except Exception:
                    continue

            if not any_mcp:
                print(f"   🔌 MCP: no stdio servers configured")
        finally:
            session.close()
    except Exception:
        pass  # Non-fatal: startup check failure should not block server start


# ---------------------------------------------------------------------------
# ADK Service Setup
# ---------------------------------------------------------------------------
def setup_services():
    """Initialize ADK services using the service registry."""
    from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
    from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
    from google.adk.cli.adk_web_server import AdkWebServer
    from google.adk.cli.utils.agent_loader import AgentLoader
    from google.adk.cli.service_registry import get_service_registry
    from google.adk.evaluation.local_eval_set_results_manager import LocalEvalSetResultsManager
    from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    # Register custom services
    registry = get_service_registry()

    def in_memory_session_factory(uri: str, **kwargs):
        kwargs.pop("agents_dir", None)
        return InMemorySessionService()

    registry.register_session_service("in_memory", in_memory_session_factory)

    # Agents directory
    agents_dir = str(BASE_DIR / "agents")
    agent_loader = AgentLoader(agents_dir)

    # Use lightweight in-memory services for standalone
    session_service = InMemorySessionService()
    artifact_service = InMemoryArtifactService()
    memory_service = InMemoryMemoryService()
    credential_service = InMemoryCredentialService()
    eval_sets_manager = LocalEvalSetsManager(agents_dir=agents_dir)
    eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=agents_dir)

    print(f"✅ Services initialized (in-memory mode)")

    # Create ADK web server
    adk_web_server = AdkWebServer(
        agent_loader=agent_loader,
        session_service=session_service,
        artifact_service=artifact_service,
        memory_service=memory_service,
        credential_service=credential_service,
        eval_sets_manager=eval_sets_manager,
        eval_set_results_manager=eval_set_results_manager,
        agents_dir=agents_dir,
    )

    # Get the FastAPI app from ADK (this provides /run_sse, /apps/..., etc.)
    adk_app = adk_web_server.get_fast_api_app(
        allow_origins=["*"],
    )

    return adk_app, adk_web_server


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------
def create_app():
    """Create the standalone FastAPI application."""
    # Initialize database (ensure tables and agents exist)
    _init_database()

    # Print MCP command availability so users get immediate feedback
    _print_mcp_command_status()

    # Setup ADK services
    adk_app, adk_web_server = setup_services()

    # Mount static files
    static_dir = BASE_DIR / "static"
    if static_dir.exists():
        adk_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Setup templates
    templates_dir = BASE_DIR / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    # --- Chat page (root endpoint) ---
    @adk_app.get("/", response_class=HTMLResponse)
    async def chat_page(request: Request):
        """Serve the standalone chat UI."""
        return templates.TemplateResponse("standalone/chat.html", {
            "request": request,
            "agent_name": ROOT_AGENT_NAME,
        })

    # --- Health check ---
    @adk_app.get("/health")
    async def health():
        return {"status": "healthy", "agent": ROOT_AGENT_NAME, "mode": "standalone"}

    print(f"✅ Standalone app created for agent: {ROOT_AGENT_NAME}")
    return adk_app


def _init_database():
    """Initialize the database and ensure agent folders exist."""
    try:
        from shared.utils.database_client import get_database_client
        db = get_database_client()
        if db.is_connected():
            print(f"✅ Database connected")

            # Ensure agent folders exist for all root agents
            from shared.utils.models import AgentConfig
            session = db.get_session()
            if session:
                try:
                    # Find agents without parents (root-level agents)
                    root_agents = session.query(AgentConfig).filter(
                        (AgentConfig.parent_agents == None) | (AgentConfig.parent_agents == '[]'),
                        AgentConfig.disabled == False
                    ).all()

                    agents_dir = BASE_DIR / "agents"
                    template_dir = BASE_DIR / "shared" / "template_agent"

                    for agent in root_agents:
                        agent_dir = agents_dir / agent.name
                        if not agent_dir.exists() and template_dir.exists():
                            import shutil
                            shutil.copytree(template_dir, agent_dir)
                            print(f"   ✅ Created agent folder: {agent.name}")
                        elif agent_dir.exists():
                            print(f"   ✓ Agent folder exists: {agent.name}")
                finally:
                    session.close()
        else:
            print(f"⚠️  Database connection failed")
    except Exception as e:
        logger.warning(f"Database initialization: {e}")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="MATE Standalone Agent Server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Server port (default: {PORT})")
    parser.add_argument("--host", default=HOST, help=f"Server host (default: {HOST})")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser on startup")
    args = parser.parse_args()

    app = create_app()

    # Open browser after a short delay
    if not args.no_browser:
        def _open_browser():
            time.sleep(2)
            url = f"http://localhost:{args.port}/"
            print(f"🌐 Opening browser: {url}")
            webbrowser.open(url)

        threading.Thread(target=_open_browser, daemon=True).start()

    # Start trigger runner for cron-based autonomous execution
    try:
        from shared.utils.trigger_runner import get_trigger_runner
        get_trigger_runner().start()
        import atexit as _atexit
        _atexit.register(lambda: get_trigger_runner().shutdown())
    except Exception as _e:
        print(f"⚠️  TriggerRunner not started: {_e}")

    print(f"🚀 Starting server on {args.host}:{args.port}")
    print(f"   Chat UI: http://localhost:{args.port}/")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
