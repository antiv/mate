"""
Who is calling a dashboard route, and in which workspace.

Every dashboard route takes ``ctx: RequestContext = Depends(get_request_context)``
(or ``get_api_request_context``, which answers 401 instead of redirecting)
instead of resolving the user and checking for an admin on its own. The context
also carries the caller's workspace, so a route filters by ``ctx.workspace_id``
and never takes the workspace from a query parameter
(documents/design/workspaces.md, D6).

The workspace is the one named by the X-MATE-Workspace header, else the one the
dashboard session switched to (PUT /dashboard/api/workspaces/current), else the
caller's default: workspace 1 for a platform administrator, else their first
membership. A workspace that doesn't exist, or that the caller is not a member of,
is a 404 when the header names it, so the answer doesn't confirm that it exists; a
session's choice the caller has since lost access to falls back to the default.
Platform administrators may open any workspace.

ctx.is_admin is a platform administrator or an owner or admin of the workspace: the
dashboard of that workspace. ctx.is_platform_admin is the installation's own
administrator, for what covers every workspace (server/dashboard_authz.py,
PLATFORM_ONLY_PATHS).
"""

from functools import cached_property
from typing import Optional, Tuple

from fastapi import HTTPException, Request

from server.auth import get_dashboard_auth_user, get_user_role, require_dashboard_auth

WORKSPACE_HEADER = "X-MATE-Workspace"
# The key in the dashboard session holding the workspace it switched to
SESSION_WORKSPACE_KEY = "workspace_id"


def session_workspace(request: Request) -> Optional[int]:
    """The workspace the dashboard session switched to, or None."""
    try:
        value = request.session.get(SESSION_WORKSPACE_KEY)
    except AssertionError:  # no SessionMiddleware in front of this app
        return None
    return value if isinstance(value, int) else None


def resolve_workspace(request: Request, user_id: str,
                      is_platform_admin: bool) -> Tuple[int, Optional[str]]:
    """(workspace id, the caller's role in it or None) for this request; 404 when the
    requested workspace is missing or closed to the caller."""
    from shared.utils.database_client import get_database_client
    from shared.utils.models import DEFAULT_WORKSPACE_ID
    from shared.utils.workspaces import first_workspace_of, member_role, workspace_is_open

    requested = request.headers.get(WORKSPACE_HEADER)
    db = get_database_client()
    session = db.get_session() if db else None
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        if requested is not None:
            workspace_id = int(requested) if requested.strip().isdigit() else None
            if workspace_id is None or not workspace_is_open(session, workspace_id):
                raise HTTPException(status_code=404, detail="Workspace not found")
            role = member_role(session, workspace_id, user_id)
            if role is None and not is_platform_admin:
                raise HTTPException(status_code=404, detail="Workspace not found")
            return workspace_id, role
        chosen = session_workspace(request)
        if chosen is not None and workspace_is_open(session, chosen):
            role = member_role(session, chosen, user_id)
            if role is not None or is_platform_admin:
                return chosen, role
        workspace_id = None if is_platform_admin else first_workspace_of(session, user_id)
        # No membership at all (a pending sign-up): the default workspace, as no member
        workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
        return workspace_id, member_role(session, workspace_id, user_id)
    finally:
        session.close()


class RequestContext:
    """The signed-in caller of a dashboard route."""

    def __init__(self, request: Request, user_id: str):
        self._request = request
        self.user_id = user_id

    @cached_property
    def role(self) -> str:
        """'admin' or 'user', as server.auth.get_user_role decides.

        Read on first use: for a single sign-on user it is a database lookup,
        and most routes never ask.
        """
        return get_user_role(self._request)

    @property
    def is_platform_admin(self) -> bool:
        """AUTH_USERNAME, or a user with the admin role: every workspace and the
        installation's settings."""
        return self.role == "admin"

    @property
    def is_admin(self) -> bool:
        """A platform administrator, or an owner or admin of the current workspace."""
        from shared.utils.workspaces import ROLE_ADMIN, ROLE_OWNER
        return self.is_platform_admin or self.workspace_role in (ROLE_OWNER, ROLE_ADMIN)

    @cached_property
    def _workspace(self) -> Tuple[int, Optional[str]]:
        return resolve_workspace(self._request, self.user_id, self.is_platform_admin)

    @property
    def workspace_id(self) -> int:
        return self._workspace[0]

    @property
    def workspace_role(self) -> Optional[str]:
        """owner, admin or member; None for a platform administrator who isn't a member."""
        return self._workspace[1]


def get_request_context(request: Request) -> RequestContext:
    """FastAPI dependency: the caller, or a redirect to the login page."""
    return RequestContext(request, require_dashboard_auth(request))


def get_api_request_context(request: Request) -> RequestContext:
    """FastAPI dependency: the caller, or 401 when nobody is signed in.

    For the routers in server/ (docs, widget keys, chat integrations), which have
    always answered 401 rather than redirecting.
    """
    user_id = get_dashboard_auth_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return RequestContext(request, user_id)
