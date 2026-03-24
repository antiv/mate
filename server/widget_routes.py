"""
Widget routes for embeddable chat on customer websites.

Provides:
- Widget API key authentication middleware
- Chat proxy endpoint (SSE streaming to ADK /run_sse)
- Widget config / session management
- Admin endpoints for agent, memory blocks, and file management (scoped by widget key)
- Dashboard endpoints for widget key CRUD
"""

import json
import logging
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, File, Form, UploadFile, Query
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates

from shared.utils.database_client import get_database_client
from shared.utils.models import WidgetApiKey, AgentConfig, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["Widget"])
admin_api_router = APIRouter(prefix="/widget/api", tags=["Widget - Admin API"])
dashboard_widget_router = APIRouter(prefix="/dashboard/api", tags=["Dashboard - Widget Keys"])

project_root = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(project_root / "templates"))

ADK_HOST: str = "localhost"
ADK_PORT: int = 8001


def configure_widget_proxy(adk_host: str, adk_port: int):
    global ADK_HOST, ADK_PORT
    ADK_HOST = adk_host
    ADK_PORT = adk_port


# ---------------------------------------------------------------------------
# Widget API Key authentication
# ---------------------------------------------------------------------------

def _lookup_widget_key(api_key: str) -> Optional[WidgetApiKey]:
    db = get_database_client()
    session = db.get_session()
    if not session:
        return None
    try:
        return session.query(WidgetApiKey).filter_by(api_key=api_key, is_active=True).first()
    finally:
        session.close()


def _extract_api_key(request: Request) -> str:
    key = request.headers.get("X-Widget-Key") or request.query_params.get("key")
    if not key:
        raise HTTPException(status_code=401, detail="Missing widget API key")
    return key


def _check_origin(request: Request, widget_key: WidgetApiKey):
    allowed = widget_key.get_allowed_origins()
    if allowed is None:
        return
    origin = request.headers.get("origin") or request.headers.get("referer", "")
    if not origin:
        return
    origin_normalised = origin.rstrip("/")
    for allowed_origin in allowed:
        if origin_normalised.startswith(allowed_origin.rstrip("/")):
            return
    raise HTTPException(status_code=403, detail="Origin not allowed for this widget key")


def verify_widget_key(request: Request) -> WidgetApiKey:
    """FastAPI dependency: validate widget API key and origin."""
    api_key = _extract_api_key(request)
    wk = _lookup_widget_key(api_key)
    if wk is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive widget API key")
    _check_origin(request, wk)
    return wk


# ---------------------------------------------------------------------------
# Widget public endpoints
# ---------------------------------------------------------------------------

@router.get("/mate-widget.js", include_in_schema=False)
async def serve_widget_js():
    """Serve the embeddable widget JavaScript."""
    js_path = project_root / "static" / "js" / "widget" / "mate-widget.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Widget JS not found")
    return FileResponse(js_path, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
async def widget_chat_page(request: Request, key: str = Query(...)):
    """Serve the lightweight chat page (loaded inside an iframe)."""
    wk = _lookup_widget_key(key)
    if wk is None:
        return HTMLResponse("<h3>Invalid widget key</h3>", status_code=401)
    widget_cfg = wk.get_widget_config()
    return templates.TemplateResponse("widget/chat.html", {
        "request": request,
        "api_key": key,
        "agent_name": wk.agent_name,
        "widget_config": json.dumps(widget_cfg),
    })


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def widget_admin_page(request: Request, key: str = Query(...)):
    """Serve the widget admin panel page."""
    wk = _lookup_widget_key(key)
    if wk is None:
        return HTMLResponse("<h3>Invalid widget key</h3>", status_code=401)
    return templates.TemplateResponse("widget/admin.html", {
        "request": request,
        "api_key": key,
        "agent_name": wk.agent_name,
        "project_id": wk.project_id,
    })


# ---------------------------------------------------------------------------
# Widget Chat API
# ---------------------------------------------------------------------------

@router.post("/api/chat")
async def widget_chat(request: Request, wk: WidgetApiKey = Depends(verify_widget_key)):
    """SSE streaming chat — proxies to ADK /run_sse scoped to the widget's agent."""
    body = await request.json()
    message_text = body.get("message", "")
    user_id = body.get("user_id", "anonymous")
    session_id = body.get("session_id", "")
    new_session = body.get("new_session", False)
    # Accept pre-built parts array (with inline_data for images)
    raw_parts = body.get("parts")

    scoped_user = f"widget_{wk.id}_{user_id}"
    app_name = wk.agent_name

    # ADK requires a valid session — create one if missing or explicitly requested
    if not session_id or new_session:
        session_id = await _create_adk_session(app_name, scoped_user)

    # Build message parts: use raw_parts if provided, else fall back to text-only
    if raw_parts and isinstance(raw_parts, list):
        message_parts = raw_parts
    else:
        message_parts = [{"text": message_text}]

    adk_payload = {
        "app_name": app_name,
        "user_id": scoped_user,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": message_parts,
        },
        "streaming": True,
    }

    target_url = f"http://{ADK_HOST}:{ADK_PORT}/run_sse"
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    client = httpx.AsyncClient(timeout=900.0)
    try:
        req = client.build_request("POST", target_url, json=adk_payload, headers=headers)
        r = await client.send(req, stream=True)

        # If ADK still says session not found, try creating + retrying once
        if r.status_code == 404:
            await r.aclose()
            session_id = await _create_adk_session(app_name, scoped_user)
            adk_payload["session_id"] = session_id
            req = client.build_request("POST", target_url, json=adk_payload, headers=headers)
            r = await client.send(req, stream=True)

        # Prepend a JSON event with the session_id so the client can persist it
        async def streamer():
            try:
                # Emit session_id as first SSE event so the client stores it
                yield f"data: {{\"session_id\":\"{session_id}\"}}\n\n".encode()
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()
                await client.aclose()

        return StreamingResponse(
            streamer(),
            status_code=r.status_code,
            headers=dict(r.headers),
            media_type=r.headers.get("content-type", "text/event-stream"),
        )
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(status_code=503, detail=f"ADK server unavailable: {e}")


async def _create_adk_session(app_name: str, user_id: str) -> str:
    """Always create a fresh ADK session and return its id."""
    url = f"http://{ADK_HOST}:{ADK_PORT}/apps/{app_name}/users/{user_id}/sessions"
    async with httpx.AsyncClient(timeout=30.0) as c:
        resp = await c.post(url, json={})
        if resp.status_code == 200:
            return resp.json().get("id", "")
        logger.error("Failed to create ADK session: %s %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail="Failed to create chat session")


@router.get("/api/config")
async def widget_config(wk: WidgetApiKey = Depends(verify_widget_key)):
    """Return widget configuration (agent name, greeting, theme)."""
    cfg = wk.get_widget_config()
    return {
        "agent_name": wk.agent_name,
        "greeting": cfg.get("greeting", ""),
        "theme": cfg.get("theme", "auto"),
        "button_color": cfg.get("button_color", ""),
        "title": cfg.get("title", wk.agent_name),
    }


# ---------------------------------------------------------------------------
# Widget Admin API — agent settings
# ---------------------------------------------------------------------------

@admin_api_router.get("/agent")
async def get_widget_agent(wk: WidgetApiKey = Depends(verify_widget_key)):
    """Get the agent config scoped to this widget key."""
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        agent = session.query(AgentConfig).filter_by(
            name=wk.agent_name, project_id=wk.project_id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"success": True, "agent": agent.to_dict()}
    finally:
        session.close()


@admin_api_router.put("/agent")
async def update_widget_agent(request: Request, wk: WidgetApiKey = Depends(verify_widget_key)):
    """Update limited agent fields (instruction, model_name, description)."""
    data = await request.json()
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        agent = session.query(AgentConfig).filter_by(
            name=wk.agent_name, project_id=wk.project_id
        ).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        for field in ("instruction", "model_name", "description"):
            if field in data:
                setattr(agent, field, data[field])
        session.commit()
        return {"success": True, "agent": agent.to_dict()}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Widget Admin API — memory blocks
# ---------------------------------------------------------------------------

@admin_api_router.get("/memory-blocks")
async def list_widget_memory_blocks(
    wk: WidgetApiKey = Depends(verify_widget_key),
    label_search: Optional[str] = None,
    value_search: Optional[str] = None,
):
    """List memory blocks scoped to this widget's project."""
    from shared.utils.memory_blocks_service import MemoryBlocksService
    db = get_database_client()
    svc = MemoryBlocksService(db)
    result = svc.list_blocks(project_id=wk.project_id, limit=1000,
                             label_search=label_search, value_search=value_search)
    if result.get("status") == "success":
        return {"success": True, "blocks": result.get("blocks", []),
                "block_count": result.get("block_count", 0)}
    return {"success": False, "error": result.get("error_message", "Failed")}


@admin_api_router.post("/memory-blocks")
async def create_widget_memory_block(request: Request, wk: WidgetApiKey = Depends(verify_widget_key)):
    data = await request.json()
    from shared.utils.memory_blocks_service import MemoryBlocksService
    db = get_database_client()
    svc = MemoryBlocksService(db)
    metadata = {}
    if data.get("character_limit"):
        metadata["limit"] = data["character_limit"]
    if data.get("read_only"):
        metadata["read_only"] = True
    result = svc.create_block(
        project_id=wk.project_id,
        label=data["label"],
        value=data.get("value", ""),
        description=data.get("description"),
        metadata=metadata if metadata else None,
    )
    if result.get("status") == "success":
        return {"success": True, "block": result}
    return {"success": False, "error": result.get("error_message", "Failed")}


@admin_api_router.put("/memory-blocks/{block_id}")
async def update_widget_memory_block(
    block_id: str, request: Request, wk: WidgetApiKey = Depends(verify_widget_key)
):
    data = await request.json()
    from shared.utils.memory_blocks_service import MemoryBlocksService
    db = get_database_client()
    svc = MemoryBlocksService(db)
    result = svc.modify_block(
        project_id=wk.project_id,
        block_id=block_id,
        value=data.get("value"),
        description=data.get("description"),
    )
    if result.get("status") == "success":
        return {"success": True, "block": result}
    return {"success": False, "error": result.get("error_message", "Failed")}


@admin_api_router.delete("/memory-blocks/{block_id}")
async def delete_widget_memory_block(block_id: str, wk: WidgetApiKey = Depends(verify_widget_key)):
    from shared.utils.memory_blocks_service import MemoryBlocksService
    db = get_database_client()
    svc = MemoryBlocksService(db)
    result = svc.delete_block(project_id=wk.project_id, block_id=block_id)
    if result.get("status") == "success":
        return {"success": True}
    return {"success": False, "error": result.get("error_message", "Failed")}


# ---------------------------------------------------------------------------
# Widget Admin API — file search
# ---------------------------------------------------------------------------

@admin_api_router.get("/files")
async def list_widget_files(wk: WidgetApiKey = Depends(verify_widget_key)):
    """List file search stores and their files for this widget's agent."""
    from shared.utils.file_search_service import FileSearchService
    db = get_database_client()
    service = FileSearchService(db)
    stores = service.get_stores_for_agent(wk.agent_name)
    result = []
    for store in stores:
        files = service.list_files_in_store(store["store_name"])
        result.append({**store, "files": files})
    return {"success": True, "stores": result}


@admin_api_router.post("/files/upload")
async def upload_widget_file(
    wk: WidgetApiKey = Depends(verify_widget_key),
    file: UploadFile = File(...),
    store_name: str = Form(...),
    display_name: str = Form(None),
):
    """Upload a file to a file search store scoped to this widget."""
    from shared.utils.tools.file_search_tools import upload_file_to_store
    from shared.utils.file_search_service import FileSearchService

    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = upload_file_to_store(file_path=tmp_path, store_name=store_name,
                                       display_name=display_name or file.filename)
        if result.get("success"):
            db = get_database_client()
            service = FileSearchService(db)
            doc_name = result.get("document_name", "")
            service.add_document(
                store_name=store_name, document_name=doc_name,
                display_name=display_name or file.filename,
                file_path=tmp_path, file_size=len(content),
                mime_type=file.content_type, status="completed",
                uploaded_by_agent=wk.agent_name,
            )
        return result
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@admin_api_router.delete("/files/{file_id}")
async def delete_widget_file(file_id: int, wk: WidgetApiKey = Depends(verify_widget_key)):
    """Delete a file from a file search store."""
    from shared.utils.models import FileSearchDocument, FileSearchStore
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        doc = session.query(FileSearchDocument).filter_by(id=file_id).first()
        if not doc:
            return {"success": False, "error": "File not found"}
        store = session.query(FileSearchStore).filter_by(id=doc.store_id).first()
        if not store:
            return {"success": False, "error": "Store not found"}
        from shared.utils.file_search_service import FileSearchService
        service = FileSearchService(db)
        success = service.delete_document(store.store_name, doc.document_name)
        return {"success": success}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Dashboard API — widget key management
# ---------------------------------------------------------------------------

def _get_dashboard_auth_dependency():
    from server.auth import get_dashboard_auth_user
    return get_dashboard_auth_user


@dashboard_widget_router.get("/widget-keys", tags=["Dashboard - Widget Keys"])
async def list_widget_keys(
    request: Request,
    project_id: Optional[int] = None,
    username: str = Depends(_get_dashboard_auth_dependency()),
):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        query = session.query(WidgetApiKey)
        if project_id is not None:
            query = query.filter_by(project_id=project_id)
        keys = [k.to_dict() for k in query.order_by(WidgetApiKey.created_at.desc()).all()]
        return {"success": True, "keys": keys}
    finally:
        session.close()


@dashboard_widget_router.post("/widget-keys", tags=["Dashboard - Widget Keys"])
async def create_widget_key(
    request: Request,
    username: str = Depends(_get_dashboard_auth_dependency()),
):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = await request.json()
    project_id = data.get("project_id")
    agent_name = data.get("agent_name")
    label = data.get("label", "")
    allowed_origins = data.get("allowed_origins")
    widget_config = data.get("widget_config")

    if not project_id or not agent_name:
        raise HTTPException(status_code=400, detail="project_id and agent_name are required")

    api_key = f"wk_{secrets.token_urlsafe(32)}"

    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        wk = WidgetApiKey(
            api_key=api_key,
            project_id=project_id,
            agent_name=agent_name,
            label=label,
            allowed_origins=json.dumps(allowed_origins) if allowed_origins else None,
            is_active=True,
            widget_config=json.dumps(widget_config) if widget_config else None,
        )
        session.add(wk)
        session.commit()
        session.refresh(wk)
        try:
            from shared.utils.audit_service import log, ACTION_KEY_CREATE, RESOURCE_WIDGET_KEY
            log(username, ACTION_KEY_CREATE, RESOURCE_WIDGET_KEY, resource_id=str(wk.id), details={"agent_name": agent_name, "project_id": project_id}, request=request)
        except Exception as e:
            logger.debug("Audit log widget key create: %s", e)
        return {"success": True, "key": wk.to_dict()}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@dashboard_widget_router.put("/widget-keys/{key_id}", tags=["Dashboard - Widget Keys"])
async def update_widget_key(
    key_id: int,
    request: Request,
    username: str = Depends(_get_dashboard_auth_dependency()),
):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = await request.json()
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        wk = session.query(WidgetApiKey).filter_by(id=key_id).first()
        if not wk:
            raise HTTPException(status_code=404, detail="Widget key not found")
        for field in ("label", "is_active", "allowed_origins", "widget_config", "agent_name"):
            if field in data:
                val = data[field]
                if field in ("allowed_origins", "widget_config") and isinstance(val, (dict, list)):
                    val = json.dumps(val)
                setattr(wk, field, val)
        session.commit()
        session.refresh(wk)
        try:
            from shared.utils.audit_service import log, ACTION_KEY_UPDATE, RESOURCE_WIDGET_KEY
            log(username, ACTION_KEY_UPDATE, RESOURCE_WIDGET_KEY, resource_id=str(key_id), details={"agent_name": wk.agent_name}, request=request)
        except Exception as e:
            logger.debug("Audit log widget key update: %s", e)
        return {"success": True, "key": wk.to_dict()}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@dashboard_widget_router.delete("/widget-keys/{key_id}", tags=["Dashboard - Widget Keys"])
async def delete_widget_key(
    key_id: int,
    request: Request,
    username: str = Depends(_get_dashboard_auth_dependency()),
):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        wk = session.query(WidgetApiKey).filter_by(id=key_id).first()
        if not wk:
            raise HTTPException(status_code=404, detail="Widget key not found")
        agent_name = wk.agent_name
        session.delete(wk)
        session.commit()
        try:
            from shared.utils.audit_service import log, ACTION_KEY_DELETE, RESOURCE_WIDGET_KEY
            log(username, ACTION_KEY_DELETE, RESOURCE_WIDGET_KEY, resource_id=str(key_id), details={"agent_name": agent_name}, request=request)
        except Exception as e:
            logger.debug("Audit log widget key delete: %s", e)
        return {"success": True}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@dashboard_widget_router.get("/widget-keys/{key_id}/embed-code", tags=["Dashboard - Widget Keys"])
async def get_embed_code(
    key_id: int,
    request: Request,
    username: str = Depends(_get_dashboard_auth_dependency()),
):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        wk = session.query(WidgetApiKey).filter_by(id=key_id).first()
        if not wk:
            raise HTTPException(status_code=404, detail="Widget key not found")

        base_url = str(request.base_url).rstrip("/")
        embed_code = (
            f'<script\n'
            f'  src="{base_url}/widget/mate-widget.js"\n'
            f'  data-key="{wk.api_key}"\n'
            f'  data-server="{base_url}"\n'
            f'></script>'
        )
        return {"success": True, "embed_code": embed_code, "api_key": wk.api_key}
    finally:
        session.close()
