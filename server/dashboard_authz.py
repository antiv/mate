"""
Deny-by-default authorization for dashboard write APIs.

Dashboard *pages* check `is_admin_user` and redirect non-admins to the Work Room,
but the JSON APIs behind them were authenticated-only: any signed-in user could
call POST /dashboard/api/users with roles=["admin"] and promote themselves.

This middleware requires admin for every state-changing request to
/dashboard/api/*, except the paths in USER_WRITABLE_PATHS.  New routes are
protected automatically — add them here only after deciding a non-admin may
call them.
"""

import logging
import re
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

PROTECTED_PREFIX = "/dashboard/api/"

# Routes a non-admin (regular SSO user) may write to. Work Room is the non-admin
# space and renaming a conversation is its only write.
USER_WRITABLE_PATHS = [
    re.compile(r"^/dashboard/api/workroom/title/?$"),
]


def _is_user_writable(path: str) -> bool:
    return any(p.match(path) for p in USER_WRITABLE_PATHS)


class DashboardAuthzMiddleware(BaseHTTPMiddleware):
    """Require admin for mutating /dashboard/api requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if (
            request.method not in WRITE_METHODS
            or not path.startswith(PROTECTED_PREFIX)
            or _is_user_writable(path)
        ):
            return await call_next(request)

        from server.auth import is_admin_user

        if is_admin_user(request):
            return await call_next(request)

        from server.auth import get_dashboard_auth_user
        actor = get_dashboard_auth_user(request) or "anonymous"
        logger.warning("Dashboard authz: denied %s %s for non-admin %s", request.method, path, actor)
        try:
            from shared.utils import audit_service
            audit_service.log(
                actor, audit_service.ACTION_RBAC_DENIAL, "dashboard_api",
                resource_id=path, details={"method": request.method}, request=request,
            )
        except Exception as exc:
            logger.debug("Audit log for denied dashboard write failed: %s", exc)

        return JSONResponse({"detail": "Admin access required"}, status_code=403)
