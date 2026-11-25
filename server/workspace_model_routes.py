"""
Workspace settings → Models and keys (documents/design/2.0-plan.md, phase 4e): the
current workspace's own provider keys and its image model.

- An owner of the workspace, or a platform administrator, adds, replaces and deletes
  a provider key, with an optional base URL. A key is stored encrypted
  (workspace_provider_keys, MATE_SECRET_KEY) and never sent back: the list shows its
  last four characters.
- An owner or admin of the workspace sets its image model (workspace_settings), which
  wins over the installation's for its agents. A workspace admin sees the keys,
  masked, and changes none (403).
- An admin sees which model groups its agents can be on, and the model that would
  answer for each now (the agent editor).
- Model calls read both on every call (shared/utils/provider_keys.py), so a change
  applies without reloading agents.

Every change writes an audit entry in the workspace, naming the provider, never the
key.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from server.member_routes import open_session
from server.request_context import RequestContext, get_api_request_context
from shared.utils import audit_service, secret_box
from shared.utils.model_groups import groups_for
from shared.utils.models import DEFAULT_WORKSPACE_ID, Workspace, WorkspaceProviderKey, WorkspaceSetting
from shared.utils.provider_keys import PROVIDERS, masked
from shared.utils.system_settings import IMAGE_MODEL
from shared.utils.workspaces import ROLE_OWNER

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/workspace-models", tags=["Dashboard - Models and keys"])

MAX_KEY = 4096
MAX_BASE_URL = 1024
# A provider whose key is optional: a local server
KEYLESS = ("ollama",)


def _require_owner(ctx: RequestContext) -> None:
    if not (ctx.is_platform_admin or ctx.workspace_role == ROLE_OWNER):
        raise HTTPException(status_code=403, detail="Only an owner of the workspace changes its keys")


def _audit(ctx: RequestContext, action: str, resource: str, resource_id: str,
           details: Dict[str, Any], request: Request) -> None:
    audit_service.log(ctx.user_id, action, resource, resource_id=resource_id, details=details,
                      request=request, workspace_id=ctx.workspace_id)


def _provider(value: Any) -> str:
    if not isinstance(value, str) or value.strip() not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {', '.join(PROVIDERS)}")
    return value.strip()


def _base_url(value: Any, provider: str, workspace_id: int) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail="base_url must be a string")
    value = (value or "").strip()
    if not value:
        if PROVIDERS[provider].base_url is None:
            raise HTTPException(status_code=400, detail=f"{provider} needs a base URL: the server it runs on")
        return None
    parsed = urlparse(value)
    if len(value) > MAX_BASE_URL or parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="base_url must be an http(s) URL")
    if workspace_id != DEFAULT_WORKSPACE_ID:
        # The server calls it from inside: not into its own network (shared/utils/outbound.py)
        from shared.utils.outbound import outbound_refusal
        refusal = outbound_refusal(value, workspace_id=workspace_id)
        if refusal:
            raise HTTPException(status_code=400, detail=f"base_url refused: {refusal}")
    return value


def _api_key(value: Any, provider: str) -> str:
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=400, detail="api_key must be a string")
    value = (value or "").strip()
    if len(value) > MAX_KEY or any(c.isspace() for c in value):
        raise HTTPException(status_code=400, detail="api_key must be one word of at most 4096 characters")
    if not value and provider not in KEYLESS:
        raise HTTPException(status_code=400, detail="api_key is required")
    return value


def _image_model(workspace_id: int) -> Dict[str, Any]:
    from shared.utils.tools.image_tools import default_image_model
    effective, source = default_image_model(workspace_id)
    return {"own": effective if source == "workspace" else "", "effective": effective, "source": source}


# Plain functions, which FastAPI runs in its threadpool: they read the database
@router.get("")
def list_models_and_keys(ctx: RequestContext = Depends(get_api_request_context)):
    """The workspace's provider keys (masked), the providers it can hold a key for, its
    image model, and what the caller may change."""
    session = open_session()
    try:
        rows = (session.query(WorkspaceProviderKey)
                .filter(WorkspaceProviderKey.workspace_id == ctx.workspace_id)
                .order_by(WorkspaceProviderKey.provider).all())
        keys = [{"provider": row.provider, "key": masked(row.api_key),
                 "readable": row.api_key is not None,
                 "base_url": row.base_url, "updated_by": row.updated_by,
                 "updated_at": row.updated_at.isoformat() if row.updated_at else None} for row in rows]
        allowed = session.query(Workspace.allow_platform_keys).filter(
            Workspace.id == ctx.workspace_id).scalar()
    finally:
        session.close()
    return {
        "keys": keys,
        "providers": [{"name": name, "base_url": spec.base_url, "needs_base_url": spec.base_url is None}
                      for name, spec in PROVIDERS.items()],
        "image_model": _image_model(ctx.workspace_id),
        "may_use_platform_keys": ctx.workspace_id == DEFAULT_WORKSPACE_ID or bool(allowed),
        "can_manage_keys": ctx.is_platform_admin or ctx.workspace_role == ROLE_OWNER,
        "can_store_keys": secret_box.key_configured(),
    }


@router.get("/groups")
def list_groups(ctx: RequestContext = Depends(get_api_request_context)):
    """The model groups an agent of the workspace can be on, each with the model that
    would answer now, or why none would; and the model of an agent without one."""
    session = open_session()
    try:
        return {"groups": groups_for(session, ctx.workspace_id), 
                "default_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash")}
    finally:
        session.close()


@router.put("/keys")
def set_provider_key(request: Request, body: Dict[str, Any] = Body(...),
                     ctx: RequestContext = Depends(get_api_request_context)):
    """Add or replace the workspace's key for a provider. The key is given every time,
    so a new base URL never receives a stored key."""
    _require_owner(ctx)
    if not secret_box.key_configured():
        raise HTTPException(status_code=409, detail="This server can't store keys: set MATE_SECRET_KEY")
    provider = _provider(body.get("provider"))
    api_key = _api_key(body.get("api_key"), provider)
    base_url = _base_url(body.get("base_url"), provider, ctx.workspace_id)
    session = open_session()
    try:
        # Twice at most: another request may add this provider's key between the read
        # and the write, and this one then replaces it
        for attempt in (1, 2):
            row = session.query(WorkspaceProviderKey).filter(
                WorkspaceProviderKey.workspace_id == ctx.workspace_id,
                WorkspaceProviderKey.provider == provider).first()
            replaced = row is not None
            if row is None:
                row = WorkspaceProviderKey(workspace_id=ctx.workspace_id, provider=provider)
                session.add(row)
            row.api_key, row.base_url, row.updated_by = api_key, base_url, ctx.user_id
            row.updated_at = datetime.now(timezone.utc)
            try:
                session.commit()
                break
            except IntegrityError:
                session.rollback()
                if attempt == 2:
                    raise HTTPException(status_code=409, detail="The key could not be saved; try again")
    finally:
        session.close()
    _audit(ctx, audit_service.ACTION_PROVIDER_KEY_SET, audit_service.RESOURCE_PROVIDER_KEY, provider,
           {"provider": provider, "base_url": base_url, "replaced": replaced}, request)
    return {"success": True, "provider": provider, "key": masked(api_key), "base_url": base_url}


@router.delete("/keys/{provider}")
def delete_provider_key(provider: str, request: Request,
                        ctx: RequestContext = Depends(get_api_request_context)):
    """Delete the workspace's key for a provider."""
    _require_owner(ctx)
    session = open_session()
    try:
        deleted = session.query(WorkspaceProviderKey).filter(
            WorkspaceProviderKey.workspace_id == ctx.workspace_id,
            WorkspaceProviderKey.provider == provider).delete()
        session.commit()
    finally:
        session.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="This workspace has no key for that provider")
    _audit(ctx, audit_service.ACTION_PROVIDER_KEY_DELETE, audit_service.RESOURCE_PROVIDER_KEY, provider,
           {"provider": provider}, request)
    return {"success": True}


@router.put("/image-model")
def set_image_model(request: Request, body: Dict[str, Any] = Body(...),
                    ctx: RequestContext = Depends(get_api_request_context)):
    """Set the workspace's image model, for its agents that name none; empty falls back
    to the installation's."""
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        raise HTTPException(status_code=400, detail="model must be a string")
    model = (model or "").strip()
    if len(model) > 255 or any(c.isspace() for c in model):
        raise HTTPException(status_code=400, detail="model must be a single name of at most 255 characters")
    if model:
        try:
            import litellm
            from shared.utils.tools.image_tools import image_model_name
            litellm.get_llm_provider(image_model_name(model))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Unknown model '{model}': write it as "
                                                        "provider/model, e.g. gemini/gemini-2.5-flash-image")
    session = open_session()
    try:
        # Twice at most, as for a key: two first saves at once
        for attempt in (1, 2):
            row = session.get(WorkspaceSetting, (ctx.workspace_id, IMAGE_MODEL))
            before = row.value if row else None
            if not model:
                if row:
                    session.delete(row)
            elif row:
                row.value, row.updated_by, row.updated_at = model, ctx.user_id, datetime.now(timezone.utc)
            else:
                session.add(WorkspaceSetting(workspace_id=ctx.workspace_id, setting_key=IMAGE_MODEL,
                                             value=model, updated_by=ctx.user_id))
            try:
                session.commit()
                break
            except IntegrityError:
                session.rollback()
                if attempt == 2:
                    raise HTTPException(status_code=409, detail="The image model could not be saved; try again")
    finally:
        session.close()
    if (before or "") != model:
        _audit(ctx, audit_service.ACTION_WORKSPACE_SETTING_UPDATE, audit_service.RESOURCE_WORKSPACE_SETTING,
               IMAGE_MODEL, {"from": before, "to": model or None}, request)
    return {"success": True, "image_model": _image_model(ctx.workspace_id)}
