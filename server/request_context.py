"""
Who is calling a dashboard route.

Every dashboard route takes ``ctx: RequestContext = Depends(get_request_context)``
instead of resolving the user and checking for an admin on its own. In 2.0 the
context also carries the caller's workspace, so scoping is added here once rather
than in every route (documents/design/2.0-plan.md, item 11).
"""

from functools import cached_property

from fastapi import Request

from server.auth import get_user_role, require_dashboard_auth


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
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_request_context(request: Request) -> RequestContext:
    """FastAPI dependency: the caller, or a redirect to the login page."""
    return RequestContext(request, require_dashboard_auth(request))
