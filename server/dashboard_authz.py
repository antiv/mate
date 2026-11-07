"""
Deny-by-default authorization for dashboard write APIs.

Dashboard *pages* check `is_admin_user` and redirect non-admins to the Work Room,
but the JSON APIs behind them were authenticated-only: any signed-in user could
call POST /dashboard/api/users with roles=["admin"] and promote themselves.

This middleware requires admin for every request to /dashboard/api/*, reads
included, except the paths in USER_WRITABLE_PATHS and USER_READABLE_PATHS.
Reads were once open to any signed-in user, which handed a regular SSO user the
user list, the audit log, every conversation and the widget admin keys. New
routes are protected automatically — add them here only after deciding a
non-admin may call them.

Admin is ctx.is_admin (server/request_context.py): a platform administrator, or an
owner or admin of the request's workspace, whose routes keep to that workspace.
PLATFORM_ONLY_PATHS act on the whole installation and need a platform
administrator (documents/design/workspaces.md, "What stays platform-only").
"""

import asyncio
import logging
import re
from typing import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

PROTECTED_PREFIX = "/dashboard/api/"

# Routes a non-admin (regular SSO user) may write to. Work Room is the non-admin
# space: renaming a conversation, and rating a reply (with an optional note).
# The account menu creates and revokes the caller's own personal access tokens;
# creating one also needs a role in ALLOWED_API_ROLES, checked in the handler.
USER_WRITABLE_PATHS = [
    re.compile(r"^/dashboard/api/workroom/title/?$"),
    re.compile(r"^/dashboard/api/workspaces/current/?$"),
    re.compile(r"^/dashboard/api/feedback/?$"),
    re.compile(r"^/dashboard/api/tokens/?$"),
    re.compile(r"^/dashboard/api/tokens/\d+/?$"),
]

# Routes a non-admin may read. The Work Room shows their own ratings, and the
# account menu their own personal access tokens; both are scoped to the caller.
USER_READABLE_PATHS = [
    re.compile(r"^/dashboard/api/feedback/?$"),
    re.compile(r"^/dashboard/api/workspaces/?$"),
    re.compile(r"^/dashboard/api/tokens/?$"),
]


# (methods, or None for every method; path). Migrations, server control, installation
# settings and system jobs, the wizard and shop, users, audit retention, the template
# catalogue, and binary builds, which pack the installation's agents/ folder.
PLATFORM_ONLY_PATHS = [
    (None, re.compile(
        r"^/dashboard/api/(migrations|server|settings|system-jobs|wizard|shop|users)(/.*)?$")),
    (None, re.compile(r"^/dashboard/api/audit-logs/retention(-config)?/?$")),
    (None, re.compile(r"^/dashboard/api/agents/build-binary(/.*)?$")),
    ({"POST"}, re.compile(r"^/dashboard/api/agents/reinitialize-all/?$")),
    ({"POST"}, re.compile(r"^/dashboard/api/templates/create-from-agents/?$")),
    ({"DELETE"}, re.compile(r"^/dashboard/api/templates/[^/]+/?$")),
]


def is_platform_only(method: str, path: str) -> bool:
    """True for a route only a platform administrator may call."""
    return any((methods is None or method in methods) and pattern.match(path)
               for methods, pattern in PLATFORM_ONLY_PATHS)


def _is_user_writable(path: str) -> bool:
    return any(p.match(path) for p in USER_WRITABLE_PATHS)


def _is_user_readable(path: str) -> bool:
    return any(p.match(path) for p in USER_READABLE_PATHS)


class DashboardAuthzMiddleware(BaseHTTPMiddleware):
    """Require admin for /dashboard/api requests, outside the user allowlists."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        is_write = request.method in WRITE_METHODS
        if (
            not path.startswith(PROTECTED_PREFIX)
            or (is_write and _is_user_writable(path))
            or (not is_write and _is_user_readable(path))
        ):
            return await call_next(request)

        from server.auth import get_dashboard_auth_user
        from server.request_context import context_for

        actor = get_dashboard_auth_user(request)
        if not actor:
            # Nobody signed in: the route's own dependency answers 401 or redirects
            return await call_next(request)
        platform_only = is_platform_only(request.method, path)

        def check() -> bool:
            # Resolving the workspace and the roles reads the database: off the event
            # loop. The route's dependency then reuses the context (context_for).
            ctx = context_for(request, actor)
            return ctx.is_platform_admin if platform_only else ctx.is_admin

        try:
            allowed = await asyncio.to_thread(check)
        except HTTPException as exc:  # a workspace the caller can't open: 404
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if allowed:
            return await call_next(request)

        logger.warning("Dashboard authz: denied %s %s for non-admin %s", request.method, path, actor)
        try:
            from shared.utils import audit_service
            audit_service.log(
                actor, audit_service.ACTION_RBAC_DENIAL, "dashboard_api",
                resource_id=path, details={"method": request.method}, request=request,
            )
        except Exception as exc:
            logger.debug("Audit log for denied dashboard write failed: %s", exc)

        detail = "Platform administrator access required" if platform_only else "Admin access required"
        return JSONResponse({"detail": detail}, status_code=403)
