#!/usr/bin/env python3
"""
Route inventory: every /dashboard/api/ route of the real app is admin-only,
except the routes reviewed in USER_ROUTES, and every /dashboard route learns who
is calling from get_request_context.

The routes are listed from the app's router, not from a hand-kept list, so a
route is covered the day it is added. test_dashboard_authz.py checks the
middleware on a stub app; this checks that it guards every route MATE serves.
2.0 grows this into the cross-workspace test (documents/design/2.0-plan.md).
"""

import os
import re
import sys
import unittest
from typing import List, Tuple
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

# auth_server sets OTEL_SDK_DISABLED on import; keep that out of other tests.
with patch.dict(os.environ):
    import auth_server
from server.dashboard_authz import (
    PROTECTED_PREFIX, WRITE_METHODS, _is_user_readable, _is_user_writable,
)
from server.request_context import get_request_context

# Routes a non-admin may call. Each was decided on and is scoped to the caller
# in its handler; see USER_WRITABLE_PATHS and USER_READABLE_PATHS.
USER_ROUTES = {
    ("GET", "/dashboard/api/feedback"),
    ("POST", "/dashboard/api/feedback"),
    ("GET", "/dashboard/api/tokens"),
    ("POST", "/dashboard/api/tokens"),
    ("DELETE", "/dashboard/api/tokens/{token_id}"),
    ("POST", "/dashboard/api/workroom/title"),
}

# Route groups (by tag) still resolving the caller on their own. Each PR of
# 1.6.0 item 2 moves groups to get_request_context and removes them here (#276).
NOT_YET_ON_CONTEXT = {
    "Dashboard - Agent Versions",
    "Dashboard - Audit",
    "Dashboard - Channel Integrations",
    "Dashboard - Docs",
    "Dashboard - Feedback",
    "Dashboard - File Search",
    "Dashboard - Guardrails",
    "Dashboard - Memory Blocks",
    "Dashboard - Migrations",
    "Dashboard - Projects",
    "Dashboard - Rate Limits",
    "Dashboard - Server Control",
    "Dashboard - Sessions",
    "Dashboard - Templates",
    "Dashboard - Traces",
    "Dashboard - Usage Analytics",
    "Dashboard - Widget Keys",
    "Dashboard - WorkRoom",
}

_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")


def _concrete(path: str) -> str:
    """The path with every parameter filled in, so it can be requested."""
    # Digits, since an allowlist entry may require them (a token id)
    return _PARAM.sub(lambda m: "1/2" if m.group(2) == "path" else "1", path)


def dashboard_api_routes(app: FastAPI) -> List[Tuple[str, str]]:
    """(method, path) for every route under /dashboard/api/, included routers too."""
    # app.routes keeps an included router as one entry; iter_route_contexts
    # yields the routes inside it.
    return sorted(
        {
            (method, ctx.path)
            for ctx in iter_route_contexts(app.routes)
            if ctx.path and ctx.path.startswith(PROTECTED_PREFIX)
            for method in ctx.methods or ()
        }
    )


def _depends_on(dependant, call) -> bool:
    return any(d.call is call or _depends_on(d, call) for d in dependant.dependencies)


def dashboard_routes(app: FastAPI) -> List[APIRoute]:
    """Every route under /dashboard, pages and APIs, included routers too."""
    return [
        ctx.original_route
        for ctx in iter_route_contexts(app.routes)
        if ctx.path and ctx.path.startswith("/dashboard") and isinstance(ctx.original_route, APIRoute)
    ]


def _group(route: APIRoute) -> str:
    return route.tags[0] if route.tags else "(no tag)"


class TestRouteInventory(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.routes = dashboard_api_routes(auth_server.app)

    def test_inventory_lists_routes_of_included_routers(self):
        # Without included routers the test would pass on a fraction of the routes
        for route in [
            ("POST", "/dashboard/api/users"),                 # dashboard_server.py
            ("GET", "/dashboard/api/widget-keys"),            # server/widget_routes.py
            ("GET", "/dashboard/api/channel-integrations"),   # server/slack_routes.py
            ("GET", "/dashboard/api/docs/pages"),             # server/docs_routes.py
        ]:
            self.assertIn(route, self.routes)

    def test_user_routes_exist(self):
        missing = USER_ROUTES - set(self.routes)
        self.assertFalse(missing, f"USER_ROUTES names routes the app does not serve: {missing}")

    def test_only_user_routes_pass_the_allowlists(self):
        passing = {
            (method, path)
            for method, path in self.routes
            if (_is_user_writable if method in WRITE_METHODS else _is_user_readable)(_concrete(path))
        }
        self.assertEqual(passing, USER_ROUTES)

    def test_non_admin_is_refused_on_every_other_route(self):
        client = TestClient(auth_server.app)
        allowed = []
        with patch("server.auth.is_admin_user", return_value=False), \
                patch("server.auth.get_dashboard_auth_user", return_value="regular_user"), \
                patch("shared.utils.audit_service.log"):
            for method, path in self.routes:
                if (method, path) in USER_ROUTES:
                    continue
                response = client.request(method, _concrete(path))
                if response.status_code != 403 or response.json() != {"detail": "Admin access required"}:
                    allowed.append(f"{method} {path} -> {response.status_code}")
        self.assertFalse(
            allowed,
            "A non-admin reached these routes. Leave them admin-only, or decide a "
            "non-admin may call them, scope them to the caller and add them to "
            "server/dashboard_authz.py and USER_ROUTES:\n" + "\n".join(allowed),
        )


class TestRequestContext(unittest.TestCase):
    """Every dashboard route takes the caller from one place (#276, item 2)."""

    @classmethod
    def setUpClass(cls):
        cls.routes = dashboard_routes(auth_server.app)

    def test_every_route_uses_the_request_context(self):
        missing = sorted(
            f"{','.join(sorted(r.methods))} {r.path} [{_group(r)}]"
            for r in self.routes
            if _group(r) not in NOT_YET_ON_CONTEXT
            and not _depends_on(r.dependant, get_request_context)
        )
        self.assertFalse(
            missing,
            "These dashboard routes do not take ctx: RequestContext = "
            "Depends(get_request_context):\n" + "\n".join(missing),
        )

    def test_groups_left_to_move_are_not_done_yet(self):
        # A group whose routes all use the context comes off NOT_YET_ON_CONTEXT
        done = {
            group for group in NOT_YET_ON_CONTEXT
            if all(_depends_on(r.dependant, get_request_context)
                   for r in self.routes if _group(r) == group)
        }
        self.assertFalse(done, f"Remove from NOT_YET_ON_CONTEXT: {done}")


if __name__ == "__main__":
    unittest.main()
