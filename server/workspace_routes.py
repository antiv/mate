"""
The workspace switcher (documents/design/workspaces.md, "Dashboard"): the workspaces a
dashboard user may open, and the one their session works in.

GET /dashboard/api/workspaces lists them. PUT /dashboard/api/workspaces/current keeps
the choice in the session, which server/request_context.py reads on every later
request without an X-MATE-Workspace header. Members may call both
(server/dashboard_authz.py allowlists them), since the Work Room follows the workspace
too. base.html shows the switcher only to a user with more than one workspace.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from server.request_context import (
    SESSION_WORKSPACE_KEY, RequestContext, get_api_request_context, resolve_workspace,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/workspaces", tags=["Dashboard - Workspaces"])


def workspaces_for(user_id: str, is_platform_admin: bool) -> List[Dict[str, Any]]:
    """The open workspaces the user may open, lowest id first, each with the user's role
    in it: every workspace for a platform administrator (role None where they are no
    member), else their memberships."""
    from shared.utils.database_client import get_database_client
    from shared.utils.models import Workspace, WorkspaceMember

    session = get_database_client().get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        query = session.query(Workspace.id, Workspace.name, WorkspaceMember.role).outerjoin(
            WorkspaceMember, (WorkspaceMember.workspace_id == Workspace.id)
            & (WorkspaceMember.user_id == user_id)).filter(Workspace.deleted_at.is_(None))
        if not is_platform_admin:
            query = query.filter(WorkspaceMember.user_id.isnot(None))
        return [{"id": workspace_id, "name": name, "role": role}
                for workspace_id, name, role in query.order_by(Workspace.id)]
    finally:
        session.close()


def switcher_for(request: Request) -> Optional[Dict[str, Any]]:
    """For base.html: {"current", "workspaces"} when the signed-in user may open more
    than one workspace, else None. Never raises: the page renders without it."""
    from server.auth import get_dashboard_auth_user, is_admin_user
    try:
        user_id = get_dashboard_auth_user(request)
        if not user_id:
            return None
        platform_admin = is_admin_user(request)
        workspaces = workspaces_for(user_id, platform_admin)
        if len(workspaces) < 2:
            return None
        return {"current": resolve_workspace(request, user_id, platform_admin)[0],
                "workspaces": workspaces}
    except Exception as e:
        logger.debug("Workspace switcher left out: %s", e)
        return None


@router.get("")
async def list_workspaces(ctx: RequestContext = Depends(get_api_request_context)):
    """The workspaces the caller may open, and the current one."""
    return {"current": ctx.workspace_id,
            "workspaces": workspaces_for(ctx.user_id, ctx.is_platform_admin)}


@router.put("/current")
async def switch_workspace(request: Request, body: Dict[str, Any] = Body(...),
                           ctx: RequestContext = Depends(get_api_request_context)):
    """Work in another workspace for the rest of this dashboard session."""
    workspace_id = body.get("workspace_id")
    if not isinstance(workspace_id, int) or isinstance(workspace_id, bool):
        raise HTTPException(status_code=400, detail="workspace_id must be an integer")
    if workspace_id not in {w["id"] for w in workspaces_for(ctx.user_id, ctx.is_platform_admin)}:
        raise HTTPException(status_code=404, detail="Workspace not found")
    request.session[SESSION_WORKSPACE_KEY] = workspace_id
    return {"current": workspace_id}
