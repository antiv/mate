"""
FastAPI app for the LangGraph runtime, emulating the ADK server's HTTP contract.

Endpoints mirror what the auth-server proxy, dashboard workroom, widget and
OpenAI-compat layer expect from adk_main.py: /list-apps, session CRUD under
/apps/{app}/users/{user}/sessions, /run_sse (SSE, ADK Event JSON frames),
artifact fetch, and the agent cache reload endpoints.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from shared.utils.langgraph.session_store import get_session_store

logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).parent.parent.parent.parent / "agents"


def list_apps() -> List[str]:
    """Enumerate available agent apps: agents/ subfolders + enabled top-level DB agents."""
    apps = set()
    if AGENTS_DIR.is_dir():
        for p in AGENTS_DIR.iterdir():
            if p.is_dir() and not p.name.startswith((".", "__")):
                apps.add(p.name)
    try:
        from shared.utils.database_client import get_database_client
        from shared.utils.models import AgentConfig
        db_client = get_database_client()
        session = db_client.get_session() if db_client else None
        if session:
            try:
                rows = session.query(AgentConfig.name).filter(
                    (AgentConfig.parent_agents == None) | (AgentConfig.parent_agents == '[]'),
                    AgentConfig.disabled == False
                ).all()
                apps.update(row.name for row in rows)
            finally:
                session.close()
    except Exception as e:
        logger.warning(f"Could not list DB agents: {e}")
    return sorted(apps)


def create_app(allow_origins: Optional[List[str]] = None) -> FastAPI:
    app = FastAPI(title="MATE LangGraph Runtime")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = get_session_store()

    @app.get("/list-apps")
    async def get_list_apps():
        return list_apps()

    @app.post("/apps/{app_name}/users/{user_id}/sessions")
    async def create_session(app_name: str, user_id: str, request: Request):
        state = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                state = body.get("state")
        except Exception:
            pass
        session = store.create_session(app_name, user_id, state=state)
        return session

    @app.post("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def create_session_with_id(app_name: str, user_id: str, session_id: str, request: Request):
        state = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                state = body.get("state")
        except Exception:
            pass
        session = store.create_session(app_name, user_id, session_id=session_id, state=state)
        if session is None:
            raise HTTPException(status_code=400, detail=f"Session already exists: {session_id}")
        return session

    @app.get("/apps/{app_name}/users/{user_id}/sessions")
    async def list_sessions(app_name: str, user_id: str):
        return store.list_sessions(app_name, user_id)

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def get_session(app_name: str, user_id: str, session_id: str):
        session = store.get_session(app_name, user_id, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return session

    @app.delete("/apps/{app_name}/users/{user_id}/sessions/{session_id}")
    async def delete_session(app_name: str, user_id: str, session_id: str):
        if not store.delete_session(app_name, user_id, session_id):
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return {}

    @app.post("/run_sse")
    async def run_sse(request: Request):
        payload = await request.json()
        app_name = payload.get("app_name") or payload.get("appName")
        user_id = payload.get("user_id") or payload.get("userId")
        session_id = payload.get("session_id") or payload.get("sessionId")
        new_message = payload.get("new_message") or payload.get("newMessage")
        if not all([app_name, user_id, session_id, new_message]):
            raise HTTPException(status_code=400, detail="app_name, user_id, session_id and new_message are required")
        if not store.session_exists(app_name, user_id, session_id):
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        from shared.utils.langgraph.runner import run_sse_stream
        return StreamingResponse(
            run_sse_stream(app_name=app_name, user_id=user_id, session_id=session_id,
                           new_message=new_message),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts")
    async def list_artifacts(app_name: str, user_id: str, session_id: str):
        from shared.utils.langgraph.artifact_adapter import get_artifact_adapter
        return await get_artifact_adapter().list_keys(
            app_name=app_name, user_id=user_id, session_id=session_id)

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{filename}/versions")
    async def list_artifact_versions(app_name: str, user_id: str, session_id: str, filename: str):
        from shared.utils.langgraph.artifact_adapter import get_artifact_adapter
        return await get_artifact_adapter().list_versions(
            app_name=app_name, user_id=user_id, session_id=session_id, filename=filename)

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{filename}/versions/{version_id}")
    async def get_artifact_version(app_name: str, user_id: str, session_id: str,
                                   filename: str, version_id: str):
        from shared.utils.langgraph.artifact_adapter import get_artifact_adapter, part_to_wire
        try:
            version = int(version_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid version: {version_id}")
        part = await get_artifact_adapter().load(
            app_name=app_name, user_id=user_id, session_id=session_id,
            filename=filename, version=version)
        wire = part_to_wire(part) if part is not None else None
        if wire is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {filename}/{version_id}")
        return wire

    @app.post("/api/reload-agent/{agent_name}")
    async def reload_agent(agent_name: str):
        from shared.utils.langgraph.agent_builder import get_agent_builder
        get_agent_builder().invalidate(agent_name)
        logger.info(f"[LangGraph] Reloaded agent cache: {agent_name}")
        return {"success": True, "message": f"Agent '{agent_name}' cache cleared"}

    @app.post("/api/reload-all-agents")
    async def reload_all_agents():
        from shared.utils.langgraph.agent_builder import get_agent_builder
        get_agent_builder().invalidate_all()
        logger.info("[LangGraph] Reloaded all agent caches")
        return {"success": True, "message": "All agent caches cleared. Agents will reload on next request."}

    return app
