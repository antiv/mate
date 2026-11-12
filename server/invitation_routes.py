"""
Workspace invitations (documents/design/2.0-plan.md, phase 3f): an owner invites an
email address to the current workspace, with a role.

- The link carries a random token, and only its SHA-256 is stored. It is valid for
  INVITATION_DAYS days and is used once.
- When SMTP_HOST is set, the link is emailed through the server's SMTP account. The
  response carries it either way, for the owner to copy.
- Inviting an address that already has an open invitation to the workspace replaces
  that invitation. Inviting a member is refused (409).
- Accepting happens on the page /dashboard/invite/{token}, which calls POST /accept.
  It needs a dashboard session whose user id or email is the invited address. It adds
  the membership with DEFAULT_AGENT_ROLES and takes a pending sign-up out of pending.
  It does not make them a member of the default workspace. Single sign-on applies
  OAUTH_ALLOWED_DOMAINS and OAUTH_ALLOWED_EMAILS before there is a session, as for any
  sign-in.
- Platform → Workspaces creates a workspace whose first owner has no account yet with
  an owner invitation (create_invitation).

Listing is for owners and admins of the workspace. Inviting and revoking are for its
owners and platform administrators. Every invitation, revocation and acceptance writes
an audit entry in the workspace.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from server.member_routes import _require_owner, _session
from server.request_context import (
    SESSION_WORKSPACE_KEY, RequestContext, get_api_request_context,
)
from shared.utils import audit_service
from shared.utils.models import User, Workspace, WorkspaceInvitation, WorkspaceMember
from shared.utils.workspaces import (
    DEFAULT_AGENT_ROLES, MEMBER_ROLES, ROLE_MEMBER, ROLE_OWNER, is_reserved_user_id, open_filters,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/invitations", tags=["Dashboard - Members"])

INVITATION_DAYS = 7
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 255
# secrets.token_urlsafe(32) is 43 characters; anything far off it is no token
TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,100}$")


def _now() -> datetime:
    """Now in UTC, naive, as the DateTime columns hold it."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invited_email(value: Any) -> str:
    """The address, stripped and lowercased; 400 when it is none."""
    email = value.strip().lower() if isinstance(value, str) else ""
    if not EMAIL.match(email) or len(email) > EMAIL_MAX:
        raise HTTPException(status_code=400, detail="email must be an email address")
    return email


def status_of(invitation: WorkspaceInvitation) -> str:
    if invitation.accepted_at is not None:
        return "accepted"
    return "expired" if _naive(invitation.expires_at) <= _now() else "open"


def describe(invitation: WorkspaceInvitation) -> Dict[str, Any]:
    return {
        "id": invitation.id, "email": invitation.email, "role": invitation.role,
        "status": status_of(invitation), "invited_by": invitation.created_by,
        "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
        "expires_at": invitation.expires_at.isoformat(),
    }


def invitation_link(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/dashboard/invite/{token}"


def create_invitation(session: Session, workspace_id: int, email: str, role: str,
                      created_by: str) -> Tuple[WorkspaceInvitation, str, int]:
    """Add an invitation, replacing the address's open one to the workspace. Returns it,
    its token and how many it replaced; the caller commits. 409 for a member."""
    member = (session.query(WorkspaceMember.id)
              .outerjoin(User, User.user_id == WorkspaceMember.user_id)
              .filter(WorkspaceMember.workspace_id == workspace_id,
                      or_(func.lower(WorkspaceMember.user_id) == email, func.lower(User.email) == email))
              .first())
    if member:
        raise HTTPException(status_code=409, detail=f"{email} is already a member of this workspace")
    replaced = session.query(WorkspaceInvitation).filter(
        WorkspaceInvitation.workspace_id == workspace_id,
        func.lower(WorkspaceInvitation.email) == email,
        WorkspaceInvitation.accepted_at.is_(None),
    ).delete(synchronize_session=False)
    token = secrets.token_urlsafe(32)
    now = _now()
    invitation = WorkspaceInvitation(workspace_id=workspace_id, email=email, role=role,
                                     token_hash=hash_token(token), created_by=created_by,
                                     created_at=now, expires_at=now + timedelta(days=INVITATION_DAYS))
    session.add(invitation)
    session.flush()
    return invitation, token, replaced


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


async def send_invitation(email: str, workspace_name: str, role: str, inviter: str, link: str) -> bool:
    """Email the link when SMTP_HOST is set; True when it was sent."""
    if not os.getenv("SMTP_HOST", ""):
        return False
    from shared.utils.notify import send_email
    subject = f"You're invited to {_one_line(workspace_name)} on MATE"
    body = (f"{_one_line(inviter)} invited you to the workspace \"{_one_line(workspace_name)}\" "
            f"on MATE, as {role}.\n\n"
            f"Open this link and sign in as {email} to accept:\n{link}\n\n"
            f"The link works once and expires in {INVITATION_DAYS} days. If you didn't expect "
            f"it, ignore this email.\n")
    ok, detail = await asyncio.to_thread(send_email, email, subject, body)
    if not ok:
        logger.warning("Invitation email to %s not sent: %s", email, detail)
    return ok


def _audit(actor: str, action: str, invitation_id: int, workspace_id: int, details: Dict[str, Any],
           request: Request) -> None:
    audit_service.log(actor, action, audit_service.RESOURCE_INVITATION, resource_id=invitation_id,
                      details=details, request=request, workspace_id=workspace_id)


def _by_token(session: Session, token: Any) -> Optional[WorkspaceInvitation]:
    if not isinstance(token, str) or not TOKEN.match(token):
        return None
    return session.query(WorkspaceInvitation).filter(
        WorkspaceInvitation.token_hash == hash_token(token)).first()


def _open_workspace(session: Session, workspace_id: int) -> Optional[Workspace]:
    return session.query(Workspace).filter(Workspace.id == workspace_id, *open_filters()).first()


def _addresses(user: Optional[User], user_id: str) -> Set[str]:
    return {a.lower() for a in (user_id, user.email if user else None) if a}


def invitation_for_page(token: str, user_id: str) -> Dict[str, Any]:
    """What /dashboard/invite/{token} shows the signed-in user. state is open, accepted,
    expired or invalid (unknown, revoked, or its workspace suspended or closed)."""
    session = _session()
    try:
        invitation = _by_token(session, token)
        workspace = _open_workspace(session, invitation.workspace_id) if invitation else None
        if workspace is None:
            return {"state": "invalid"}
        user = session.query(User).filter(User.user_id == user_id).first()
        member = session.query(WorkspaceMember.id).filter(
            WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == user_id).first()
        return {"state": status_of(invitation), "workspace": workspace.name, "role": invitation.role,
                "email": invitation.email, "invited_by": invitation.created_by,
                "for_you": invitation.email.lower() in _addresses(user, user_id),
                "member": member is not None}
    finally:
        session.close()


@router.get("")
async def list_invitations(ctx: RequestContext = Depends(get_api_request_context)):
    """The current workspace's invitations not accepted yet, newest first."""
    session = _session()
    try:
        rows = (session.query(WorkspaceInvitation)
                .filter(WorkspaceInvitation.workspace_id == ctx.workspace_id,
                        WorkspaceInvitation.accepted_at.is_(None))
                .order_by(WorkspaceInvitation.id.desc()).limit(500))
        invitations = [describe(i) for i in rows]
    finally:
        session.close()
    return {"invitations": invitations, "roles": list(MEMBER_ROLES), "days": INVITATION_DAYS,
            "emails": bool(os.getenv("SMTP_HOST", "")),
            "can_manage": ctx.is_platform_admin or ctx.workspace_role == ROLE_OWNER}


@router.post("")
async def invite(request: Request, body: Dict[str, Any] = Body(...),
                 ctx: RequestContext = Depends(get_api_request_context)):
    """Invite an email address to the current workspace: email, and role (owner, admin
    or member; default member)."""
    _require_owner(ctx)
    email = invited_email(body.get("email"))
    role = body.get("role", ROLE_MEMBER)
    if role not in MEMBER_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {', '.join(MEMBER_ROLES)}")
    session = _session()
    try:
        workspace_name = session.query(Workspace.name).filter(Workspace.id == ctx.workspace_id).scalar()
        invitation, token, replaced = create_invitation(session, ctx.workspace_id, email, role, ctx.user_id)
        session.commit()
        result = describe(invitation)
    finally:
        session.close()
    link = invitation_link(request, token)
    emailed = await send_invitation(email, workspace_name or "", role, ctx.user_id, link)
    _audit(ctx.user_id, audit_service.ACTION_INVITATION_CREATE, result["id"], ctx.workspace_id,
           {"email": email, "role": role, "emailed": emailed, "replaced": replaced}, request)
    return {"invitation": result, "link": link, "emailed": emailed}


@router.delete("/{invitation_id}")
async def revoke_invitation(invitation_id: int, request: Request,
                            ctx: RequestContext = Depends(get_api_request_context)):
    """Revoke an invitation of the current workspace that was not accepted yet."""
    _require_owner(ctx)
    session = _session()
    try:
        invitation = session.query(WorkspaceInvitation).filter(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == ctx.workspace_id).first()
        if invitation is None:
            raise HTTPException(status_code=404, detail="Invitation not found")
        if invitation.accepted_at is not None:
            raise HTTPException(status_code=409, detail="This invitation was accepted already")
        details = {"email": invitation.email, "role": invitation.role}
        session.delete(invitation)
        session.commit()
    finally:
        session.close()
    _audit(ctx.user_id, audit_service.ACTION_INVITATION_REVOKE, invitation_id, ctx.workspace_id,
           details, request)
    return {"success": True}


@router.post("/accept")
async def accept_invitation(request: Request, body: Dict[str, Any] = Body(...),
                            ctx: RequestContext = Depends(get_api_request_context)):
    """Accept an invitation as the signed-in user, whose id or email must be the invited
    address. The session then works in the workspace joined."""
    session = _session()
    try:
        invitation = _by_token(session, body.get("token"))
        if invitation is None or _open_workspace(session, invitation.workspace_id) is None:
            raise HTTPException(status_code=404, detail="This invitation is not valid. It may "
                                                        "have been revoked.")
        status = status_of(invitation)
        if status != "open":
            raise HTTPException(status_code=410, detail=f"This invitation was {status}. "
                                                        "Ask for a new one.")
        user = session.query(User).filter(User.user_id == ctx.user_id).first()
        if (user is None or is_reserved_user_id(ctx.user_id) or "widget" in user.get_roles()
                or invitation.email.lower() not in _addresses(user, ctx.user_id)):
            raise HTTPException(status_code=403, detail=f"This invitation is for {invitation.email}. "
                                                        "Sign in with that address to accept it.")
        workspace_id = invitation.workspace_id
        if session.query(WorkspaceMember.id).filter(WorkspaceMember.workspace_id == workspace_id,
                                                    WorkspaceMember.user_id == ctx.user_id).first():
            raise HTTPException(status_code=409, detail="You are already a member of this workspace")
        # Used once, also by two requests at the same time
        claimed = session.query(WorkspaceInvitation).filter(
            WorkspaceInvitation.id == invitation.id, WorkspaceInvitation.accepted_at.is_(None),
        ).update({"accepted_at": _now(), "accepted_by": ctx.user_id}, synchronize_session=False)
        if claimed != 1:
            session.rollback()
            raise HTTPException(status_code=410, detail="This invitation was accepted. Ask for a new one.")
        session.add(WorkspaceMember(workspace_id=workspace_id, user_id=ctx.user_id, role=invitation.role,
                                    agent_roles=json.dumps(DEFAULT_AGENT_ROLES),
                                    invited_by=invitation.created_by))
        # The membership first: a sign-up leaving pending as a member nowhere would join
        # the default workspace (shared/utils/workspaces.py, _member_for_changed_roles)
        session.flush()
        roles = user.get_roles()
        if "pending" in roles:
            user.roles = json.dumps([r for r in roles if r != "pending"])
        session.commit()
        details = {"email": invitation.email, "role": invitation.role, "was_pending": "pending" in roles}
        invitation_id = invitation.id
    finally:
        session.close()
    request.session[SESSION_WORKSPACE_KEY] = workspace_id
    _audit(ctx.user_id, audit_service.ACTION_INVITATION_ACCEPT, invitation_id, workspace_id, details, request)
    return {"success": True, "workspace_id": workspace_id}
