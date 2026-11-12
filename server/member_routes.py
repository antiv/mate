"""
Workspace settings → Members (documents/design/2.0-plan.md, phase 3e): who belongs to
the current workspace, with what role and agent roles.

- An owner of the workspace, or a platform administrator, changes a member's role
  (owner, admin or member) and agent roles (matched against an agent's
  allowed_for_roles), and removes a member.
- A workspace admin sees the members and changes nothing (403).
- Any member leaves the workspace (server/dashboard_authz.py allowlists
  POST /leave).
- The last owner can't be demoted, removed or leave (409), so a workspace always has
  one.
- Agent roles are names, and never a platform role (admin, pending, widget): those
  stay in users.roles (shared/utils/workspaces.py).

Every change writes an audit entry in the workspace.
"""

import json
import logging
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.request_context import (
    SESSION_WORKSPACE_KEY, RequestContext, get_api_request_context,
)
from shared.utils import audit_service
from shared.utils.models import User, WorkspaceMember, parse_roles
from shared.utils.workspaces import MEMBER_ROLES, PLATFORM_ROLES, ROLE_OWNER

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/members", tags=["Dashboard - Members"])

# An agent role: letters, digits, _ . and -, as allowed_for_roles holds them
AGENT_ROLE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_AGENT_ROLES = 50


def _session() -> Session:
    from shared.utils.database_client import get_database_client
    session = get_database_client().get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return session


def _require_owner(ctx: RequestContext) -> None:
    if not (ctx.is_platform_admin or ctx.workspace_role == ROLE_OWNER):
        raise HTTPException(status_code=403, detail="Only an owner of the workspace changes its members")


def _member(session: Session, workspace_id: int, user_id: str) -> WorkspaceMember:
    member = session.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id).first()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _keeps_an_owner(session: Session, member: WorkspaceMember) -> None:
    """409 when the member is the workspace's last owner."""
    if member.role != ROLE_OWNER:
        return
    owners = session.query(func.count(WorkspaceMember.id)).filter(
        WorkspaceMember.workspace_id == member.workspace_id,
        WorkspaceMember.role == ROLE_OWNER).scalar()
    if owners <= 1:
        raise HTTPException(status_code=409, detail="The workspace's last owner can't be demoted, "
                                                    "removed or leave. Make someone else an owner first.")


def _agent_roles(value: Any) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_AGENT_ROLES:
        raise HTTPException(status_code=400, detail="agent_roles must be a list of role names")
    roles: List[str] = []
    for role in value:
        if not isinstance(role, str) or not AGENT_ROLE.match(role.strip()):
            raise HTTPException(status_code=400, detail="An agent role is 1 to 64 letters, digits, "
                                                        "'_', '.' or '-'")
        role = role.strip()
        if role in PLATFORM_ROLES:
            raise HTTPException(status_code=400, detail=f"'{role}' is a platform role, not an agent role")
        if role not in roles:
            roles.append(role)
    return roles


def _audit(ctx: RequestContext, action: str, user_id: str, details: Dict[str, Any],
           request: Request) -> None:
    audit_service.log(ctx.user_id, action, audit_service.RESOURCE_MEMBER, resource_id=user_id,
                      details=details, request=request, workspace_id=ctx.workspace_id)


@router.get("")
async def list_members(ctx: RequestContext = Depends(get_api_request_context)):
    """The current workspace's members, and whether the caller may change them."""
    session = _session()
    try:
        rows = (session.query(WorkspaceMember, User.display_name, User.email)
                .outerjoin(User, User.user_id == WorkspaceMember.user_id)
                .filter(WorkspaceMember.workspace_id == ctx.workspace_id)
                .order_by(WorkspaceMember.role != ROLE_OWNER, WorkspaceMember.user_id))
        members = [{
            "user_id": member.user_id, "display_name": display_name, "email": email,
            "role": member.role, "agent_roles": parse_roles(member.agent_roles),
            "invited_by": member.invited_by,
            "joined_at": member.created_at.isoformat() if member.created_at else None,
        } for member, display_name, email in rows]
    finally:
        session.close()
    return {"members": members, "me": ctx.user_id, "roles": list(MEMBER_ROLES),
            "can_manage": ctx.is_platform_admin or ctx.workspace_role == ROLE_OWNER}


@router.post("/leave")
async def leave_workspace(request: Request, ctx: RequestContext = Depends(get_api_request_context)):
    """Leave the current workspace."""
    session = _session()
    try:
        member = _member(session, ctx.workspace_id, ctx.user_id)
        _keeps_an_owner(session, member)
        role = member.role
        session.delete(member)
        session.commit()
    finally:
        session.close()
    request.session.pop(SESSION_WORKSPACE_KEY, None)
    _audit(ctx, audit_service.ACTION_MEMBER_LEAVE, ctx.user_id, {"role": role}, request)
    return {"success": True}


@router.put("/{user_id}")
async def update_member(user_id: str, request: Request, body: Dict[str, Any] = Body(...),
                        ctx: RequestContext = Depends(get_api_request_context)):
    """Change a member's role, agent roles or both."""
    _require_owner(ctx)
    session = _session()
    try:
        member = _member(session, ctx.workspace_id, user_id)
        if "role" not in body and "agent_roles" not in body:
            raise HTTPException(status_code=400, detail="Give role, agent_roles or both")
        if "role" in body and body["role"] not in MEMBER_ROLES:
            raise HTTPException(status_code=400, detail=f"role must be one of {', '.join(MEMBER_ROLES)}")
        wanted_roles = _agent_roles(body["agent_roles"]) if "agent_roles" in body else None
        changes: Dict[str, Any] = {}
        if "role" in body and body["role"] != member.role:
            if member.role == ROLE_OWNER:
                _keeps_an_owner(session, member)
            changes["role"] = {"from": member.role, "to": body["role"]}
            member.role = body["role"]
        current_roles = parse_roles(member.agent_roles)
        if wanted_roles is not None and wanted_roles != current_roles:
            changes["agent_roles"] = {"from": current_roles, "to": wanted_roles}
            member.agent_roles = json.dumps(wanted_roles)
        if changes:
            session.commit()
    finally:
        session.close()
    if changes:
        _audit(ctx, audit_service.ACTION_MEMBER_UPDATE, user_id, changes, request)
    return {"success": True, "changed": sorted(changes)}


@router.delete("/{user_id}")
async def remove_member(user_id: str, request: Request,
                        ctx: RequestContext = Depends(get_api_request_context)):
    """Remove a member from the workspace. Their account stays."""
    _require_owner(ctx)
    session = _session()
    try:
        member = _member(session, ctx.workspace_id, user_id)
        _keeps_an_owner(session, member)
        details = {"role": member.role, "agent_roles": parse_roles(member.agent_roles)}
        session.delete(member)
        session.commit()
    finally:
        session.close()
    _audit(ctx, audit_service.ACTION_MEMBER_REMOVE, user_id, details, request)
    return {"success": True}
