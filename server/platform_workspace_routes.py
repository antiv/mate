"""
Platform → Workspaces (documents/design/2.0-plan.md, phase 3d): the installation's
workspaces, for platform administrators.

A platform administrator lists every workspace, closed ones included, creates one
with a first owner, renames it, sets "may use
platform keys" and its platform features, suspends and resumes it, and closes it.
- Suspending sets suspended_at and closing sets deleted_at. Either stops the
  workspace (shared/utils/workspaces.py, open_filters); resuming undoes a suspension,
  and nothing undoes closing.
- The default workspace can't be suspended or closed, and keeps every platform
  feature, as 1.x has them. A workspace that still has projects can't be closed.
- The first owner is an existing user, or an email address invited as owner
  (server/invitation_routes.py): the workspace has no member until it is accepted.
- "May use platform keys" is stored only; nothing reads it before phase 4.
- The list shows each workspace's usage over the last 30 days: tokens and cost of
  its token_usage_logs rows (a row with no workspace counts as the default one's).

Every change is written to the audit log, in the workspace it changed.
server/dashboard_authz.py makes /dashboard/api/platform/ platform-only, and each
route checks it again.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.request_context import RequestContext, get_api_request_context
from shared.utils import audit_service
from server.invitation_routes import (
    _now, create_invitation, invitation_link, invited_email, send_invitation,
)
from server.member_routes import _session
from shared.utils.models import (
    DEFAULT_WORKSPACE_ID, PLATFORM_FEATURES, Project, TokenUsageLog, User, Workspace,
    WorkspaceInvitation, WorkspaceMember,
)
from shared.utils.workspaces import (
    DEFAULT_AGENT_ROLES, NON_MEMBER_ROLES, ROLE_OWNER, is_reserved_user_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/platform/workspaces", tags=["Dashboard - Platform"])

# Lowercase letters, digits and inner hyphens, at most 40 characters (workspaces.slug)
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
NAME_MAX = 255


def _platform_admin(ctx: RequestContext = Depends(get_api_request_context)) -> RequestContext:
    if not ctx.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access required")
    return ctx


def _status(workspace: Workspace) -> str:
    if workspace.deleted_at is not None:
        return "closed"
    return "suspended" if workspace.suspended_at is not None else "open"


def _features(workspace: Workspace) -> List[str]:
    try:
        features = json.loads(workspace.platform_features or "[]")
    except (TypeError, ValueError):
        return []
    return [f for f in PLATFORM_FEATURES if f in features]


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


USAGE_DAYS = 30


def _describe(workspace: Workspace, owners: List[str], members: int, projects: int,
              invited_owners: Optional[List[str]] = None,
              usage: Optional[Tuple[int, float]] = None) -> Dict[str, Any]:
    tokens, cost = usage or (0, 0.0)
    return {
        "id": workspace.id, "name": workspace.name, "slug": workspace.slug,
        "status": _status(workspace), "is_default": workspace.id == DEFAULT_WORKSPACE_ID,
        "allow_platform_keys": bool(workspace.allow_platform_keys),
        "platform_features": _features(workspace),
        "owners": owners, "invited_owners": invited_owners or [], "members": members,
        "projects": projects, "usage": {"days": USAGE_DAYS, "tokens": tokens, "cost_usd": round(cost, 4)},
        "created_at": _iso(workspace.created_at), "suspended_at": _iso(workspace.suspended_at),
        "closed_at": _iso(workspace.deleted_at),
    }


def _list(session: Session) -> List[Dict[str, Any]]:
    members = dict(session.query(WorkspaceMember.workspace_id, func.count(WorkspaceMember.id))
                   .group_by(WorkspaceMember.workspace_id))
    projects = dict(session.query(Project.workspace_id, func.count(Project.id))
                    .group_by(Project.workspace_id))
    owners: Dict[int, List[str]] = {}
    for workspace_id, user_id in (session.query(WorkspaceMember.workspace_id, WorkspaceMember.user_id)
                                  .filter(WorkspaceMember.role == ROLE_OWNER)
                                  .order_by(WorkspaceMember.user_id)):
        owners.setdefault(workspace_id, []).append(user_id)
    invited: Dict[int, List[str]] = {}
    for workspace_id, email in (session.query(WorkspaceInvitation.workspace_id, WorkspaceInvitation.email)
                                .filter(WorkspaceInvitation.role == ROLE_OWNER,
                                        WorkspaceInvitation.accepted_at.is_(None),
                                        WorkspaceInvitation.expires_at > _now())
                                .order_by(WorkspaceInvitation.email)):
        invited.setdefault(workspace_id, []).append(email)
    usage = _usage(session)
    return [_describe(w, owners.get(w.id, []), members.get(w.id, 0), projects.get(w.id, 0),
                      invited.get(w.id), usage.get(w.id))
            for w in session.query(Workspace).order_by(Workspace.id)]


def _usage(session: Session) -> Dict[int, Tuple[int, float]]:
    """{workspace id: (tokens, dollars)} over the last USAGE_DAYS days."""
    since = datetime.now(timezone.utc) - timedelta(days=USAGE_DAYS)
    workspace = func.coalesce(TokenUsageLog.workspace_id, DEFAULT_WORKSPACE_ID)
    rows = (session.query(workspace,
                          func.sum(func.coalesce(TokenUsageLog.prompt_tokens, 0)
                                   + func.coalesce(TokenUsageLog.response_tokens, 0)),
                          func.sum(TokenUsageLog.cost_usd))
            .filter(TokenUsageLog.timestamp >= since).group_by(workspace))
    return {workspace_id: (int(tokens or 0), float(cost or 0.0)) for workspace_id, tokens, cost in rows}


def _one(session: Session, workspace_id: int) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _not_closed(workspace: Workspace) -> None:
    if workspace.deleted_at is not None:
        raise HTTPException(status_code=409, detail="This workspace is closed")


def _name(value: Any) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > NAME_MAX:
        raise HTTPException(status_code=400, detail=f"name must be 1 to {NAME_MAX} characters")
    return name


def _feature_list(value: Any) -> List[str]:
    if not isinstance(value, list) or any(f not in PLATFORM_FEATURES for f in value):
        raise HTTPException(status_code=400,
                            detail=f"platform_features must be a list of {', '.join(PLATFORM_FEATURES)}")
    return [f for f in PLATFORM_FEATURES if f in value]


def _flag(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be true or false")
    return value


def _name_taken(session: Session, name: str, workspace_id: Optional[int] = None) -> None:
    """409 when another workspace, closed ones included, has the name (workspaces.name is unique)."""
    query = session.query(Workspace.id).filter(func.lower(Workspace.name) == name.lower())
    if workspace_id is not None:
        query = query.filter(Workspace.id != workspace_id)
    if query.first():
        raise HTTPException(status_code=409, detail="Another workspace has this name")


def _owner_agent_roles(session: Session, user_id: Any) -> str:
    """The first owner's agent_roles as stored: those of their membership of the
    default workspace, else the default ones. 400 for anyone who can't be a member: no
    such user, a widget visitor, a system caller or a pending sign-up."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=400, detail="owner must be the id of an existing user")
    user = session.query(User).filter(User.user_id == user_id.strip()).first()
    if (user is None or is_reserved_user_id(user.user_id)
            or NON_MEMBER_ROLES.intersection(user.get_roles())):
        raise HTTPException(status_code=400,
                            detail="owner must be an existing user who is not pending or a widget visitor")
    raw = session.query(WorkspaceMember.agent_roles).filter(
        WorkspaceMember.workspace_id == DEFAULT_WORKSPACE_ID,
        WorkspaceMember.user_id == user.user_id).scalar()
    return raw or json.dumps(DEFAULT_AGENT_ROLES)


def _audit(ctx: RequestContext, action: str, workspace_id: int, details: Dict[str, Any],
           request: Request) -> None:
    audit_service.log(ctx.user_id, action, audit_service.RESOURCE_WORKSPACE, resource_id=workspace_id,
                      details=details, request=request, workspace_id=workspace_id)


@router.get("")
async def list_workspaces(ctx: RequestContext = Depends(_platform_admin)):
    """Every workspace, closed ones included, with its owners and counts."""
    session = _session()
    try:
        return {"workspaces": _list(session), "features": list(PLATFORM_FEATURES)}
    finally:
        session.close()


@router.post("")
async def create_workspace(request: Request, body: Dict[str, Any] = Body(...),
                           ctx: RequestContext = Depends(_platform_admin)):
    """Create a workspace: name, slug, its first owner as either owner (an existing
    user's id) or owner_email (an address invited as owner), and optionally
    allow_platform_keys (default true) and platform_features (default none)."""
    name = _name(body.get("name"))
    slug = body.get("slug")
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise HTTPException(status_code=400, detail="slug must be 1 to 40 lowercase letters, digits "
                                                    "and hyphens, starting and ending with a letter or digit")
    allow_keys = _flag(body.get("allow_platform_keys", True), "allow_platform_keys")
    features = _feature_list(body.get("platform_features", []))
    if ("owner" in body) == ("owner_email" in body):
        raise HTTPException(status_code=400, detail="Give owner (a user id) or owner_email, not both")
    owner_email = invited_email(body["owner_email"]) if "owner_email" in body else None
    session = _session()
    try:
        _name_taken(session, name)
        if session.query(Workspace.id).filter(Workspace.slug == slug).first():
            raise HTTPException(status_code=409, detail="Another workspace has this slug")
        owner = None
        if owner_email is None:
            agent_roles = _owner_agent_roles(session, body.get("owner"))
            owner = body["owner"].strip()
        workspace = Workspace(name=name, slug=slug, allow_platform_keys=allow_keys,
                              platform_features=json.dumps(features))
        session.add(workspace)
        session.flush()
        token = None
        if owner is not None:
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=owner, role=ROLE_OWNER,
                                        agent_roles=agent_roles, invited_by=ctx.user_id))
        else:
            invitation, token, _ = create_invitation(session, workspace.id, owner_email, ROLE_OWNER,
                                                     ctx.user_id)
        session.commit()
        workspace_id = workspace.id
        invitation_id = invitation.id if token else None
        result = _describe(workspace, [owner] if owner else [], 1 if owner else 0, 0,
                           [owner_email] if token else None)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Another workspace has this name or slug")
    finally:
        session.close()
    details = {"name": name, "slug": slug, "allow_platform_keys": allow_keys,
               "platform_features": features}
    details.update({"owner": owner} if owner else {"owner_invited": owner_email})
    _audit(ctx, audit_service.ACTION_WORKSPACE_CREATE, workspace_id, details, request)
    if token is None:
        return {"workspace": result}
    link = invitation_link(request, token)
    emailed = await send_invitation(owner_email, name, ROLE_OWNER, ctx.user_id, link)
    audit_service.log(ctx.user_id, audit_service.ACTION_INVITATION_CREATE,
                      audit_service.RESOURCE_INVITATION, resource_id=invitation_id,
                      details={"email": owner_email, "role": ROLE_OWNER, "emailed": emailed, "replaced": 0},
                      request=request, workspace_id=workspace_id)
    return {"workspace": result, "invitation": {"link": link, "emailed": emailed}}


@router.put("/{workspace_id}")
async def update_workspace(workspace_id: int, request: Request, body: Dict[str, Any] = Body(...),
                           ctx: RequestContext = Depends(_platform_admin)):
    """Change any of name, allow_platform_keys and platform_features."""
    wanted: Dict[str, Any] = {}
    if "name" in body:
        wanted["name"] = _name(body["name"])
    if "allow_platform_keys" in body:
        wanted["allow_platform_keys"] = _flag(body["allow_platform_keys"], "allow_platform_keys")
    if "platform_features" in body:
        wanted["platform_features"] = _feature_list(body["platform_features"])
    if not wanted:
        raise HTTPException(status_code=400,
                            detail="Give name, allow_platform_keys or platform_features")
    session = _session()
    try:
        workspace = _one(session, workspace_id)
        _not_closed(workspace)
        current = {"name": workspace.name, "allow_platform_keys": bool(workspace.allow_platform_keys),
                   "platform_features": _features(workspace)}
        changes = {field: {"from": current[field], "to": value}
                   for field, value in wanted.items() if current[field] != value}
        if "platform_features" in changes and workspace_id == DEFAULT_WORKSPACE_ID:
            raise HTTPException(status_code=409,
                                detail="The default workspace keeps every platform feature")
        if "name" in changes:
            _name_taken(session, wanted["name"], workspace_id)
            workspace.name = wanted["name"]
        if "allow_platform_keys" in changes:
            workspace.allow_platform_keys = wanted["allow_platform_keys"]
        if "platform_features" in changes:
            workspace.platform_features = json.dumps(wanted["platform_features"])
        if changes:
            session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Another workspace has this name")
    finally:
        session.close()
    if changes:
        _audit(ctx, audit_service.ACTION_WORKSPACE_UPDATE, workspace_id, changes, request)
    return {"success": True, "changed": sorted(changes)}


def _set_state(workspace_id: int, request: Request, ctx: RequestContext, action: str) -> Dict[str, Any]:
    """Suspend, resume or close the workspace; a no-op when it is already so."""
    session = _session()
    try:
        workspace = _one(session, workspace_id)
        _not_closed(workspace)
        if action != audit_service.ACTION_WORKSPACE_RESUME and workspace_id == DEFAULT_WORKSPACE_ID:
            raise HTTPException(status_code=409, detail="The default workspace can't be suspended or closed")
        details: Dict[str, Any] = {"name": workspace.name}
        now = datetime.now(timezone.utc)
        if action == audit_service.ACTION_WORKSPACE_SUSPEND:
            changed = workspace.suspended_at is None
            workspace.suspended_at = workspace.suspended_at or now
        elif action == audit_service.ACTION_WORKSPACE_RESUME:
            changed = workspace.suspended_at is not None
            workspace.suspended_at = None
        else:
            projects = session.query(func.count(Project.id)).filter(
                Project.workspace_id == workspace_id).scalar()
            if projects:
                raise HTTPException(status_code=409, detail=(
                    f"This workspace still has {projects} project(s). Delete them before closing it."))
            changed = True
            workspace.deleted_at = now
        if changed:
            session.commit()
        status = _status(workspace)
    finally:
        session.close()
    if changed:
        _audit(ctx, action, workspace_id, details, request)
    return {"success": True, "status": status}


@router.post("/{workspace_id}/suspend")
async def suspend_workspace(workspace_id: int, request: Request,
                            ctx: RequestContext = Depends(_platform_admin)):
    """Stop the workspace until it is resumed: its dashboard, tokens, agents, widget
    keys, Slack, triggers and alert rules."""
    return _set_state(workspace_id, request, ctx, audit_service.ACTION_WORKSPACE_SUSPEND)


@router.post("/{workspace_id}/resume")
async def resume_workspace(workspace_id: int, request: Request,
                           ctx: RequestContext = Depends(_platform_admin)):
    """Undo a suspension."""
    return _set_state(workspace_id, request, ctx, audit_service.ACTION_WORKSPACE_RESUME)


@router.post("/{workspace_id}/close")
async def close_workspace(workspace_id: int, request: Request,
                          ctx: RequestContext = Depends(_platform_admin)):
    """Close a workspace with no projects, for good. It keeps its name and slug."""
    return _set_state(workspace_id, request, ctx, audit_service.ACTION_WORKSPACE_CLOSE)
