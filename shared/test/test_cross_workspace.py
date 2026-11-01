#!/usr/bin/env python3
"""
The cross-workspace test (documents/design/workspaces.md, "Testing"): no route of the
real app shows or changes the data of a workspace other than the caller's.

Two workspaces: A is the default workspace, B a second one, each with a project,
agents, a member and a personal access token. B's names all contain B_MARKER. A
platform administrator working in A (X-MATE-Workspace: 1) calls:
- every GET route under /dashboard/api/, and none may answer with B_MARKER;
- every route under /dashboard/api/ that names a row in its path, with B's ids, and
  each must answer 404.

The routes are listed from the app's router, so a route is covered the day it is
added. Phase 2 scopes them group by group: NOT_YET_SCOPED lists the routes still to
do, and is empty when phase 2 is done. PLATFORM_ONLY lists the routes that act on the
whole installation; they stay for platform administrators.

The agent runtime is checked the same way: the RBAC check, the proxy and
/list-apps, the OpenAI API, MCP and the Work Room, called with A's credentials on
B's agents.
"""

import base64
import json
import os
import sys
import unittest
from typing import Dict, List, Set, Tuple
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, params
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# auth_server sets OTEL_SDK_DISABLED on import; keep that out of other tests.
with patch.dict(os.environ):
    import auth_server
from server import auth, proxy_routes
from server.dashboard_authz import PROTECTED_PREFIX, WRITE_METHODS
from server.request_context import WORKSPACE_HEADER, resolve_workspace
from server.pat_auth import verify_pat_hash
from shared.utils import database_client
from shared.utils.mcp.mcp_auth import agent_allowed
from shared.utils.models import (
    AgentConfig, AuditLog, Base, PersonalAccessToken, Project, User, WidgetApiKey, Workspace,
    WorkspaceMember,
)
from shared.utils.rbac_middleware import RBACMiddleware
from shared.utils.user_service import UserService
from shared.utils.workspaces import sync_default_members

B_MARKER = "zz-ws-b"
A, B = 1, 2
ALICE, BOB = "alice@a.example", "bob@b.example"
A_ROOT, B_ROOT = "ws_a_root", "zz_ws_b_root"
PATS = {ALICE: "mate_pat_alice_in_a", BOB: "mate_pat_bob_in_b"}

Route = Tuple[str, str]


def _routes(listing: str) -> Set[Route]:
    """{(method, path)} from lines of "METHOD /path"."""
    return {tuple(line.split(" ", 1)) for line in listing.strip().splitlines()}


# Routes that act on the whole installation (documents/design/workspaces.md, "What
# stays platform-only"). Phase 2e refuses them to workspace administrators.
PLATFORM_ONLY: Set[Route] = _routes("""
GET /dashboard/api/migrations
DELETE /dashboard/api/migrations/{version}
POST /dashboard/api/migrations/{version}/rerun
POST /dashboard/api/migrations/run
GET /dashboard/api/server/status
POST /dashboard/api/server/start
POST /dashboard/api/server/stop
POST /dashboard/api/server/restart
GET /dashboard/api/settings/image-model
PUT /dashboard/api/settings/image-model
GET /dashboard/api/settings/model-prices
PUT /dashboard/api/settings/model-prices
DELETE /dashboard/api/settings/model-prices
POST /dashboard/api/settings/model-prices/fill-missing
GET /dashboard/api/system-jobs
POST /dashboard/api/system-jobs/{job_id}/toggle
POST /dashboard/api/system-jobs/{job_id}/reschedule
GET /dashboard/api/wizard/leads
PATCH /dashboard/api/wizard/leads/{lead_id}
POST /dashboard/api/wizard/leads/{lead_id}/convert
GET /dashboard/api/wizard/leads/{lead_id}/snapshot
DELETE /dashboard/api/wizard/leads/{lead_id}
GET /dashboard/api/wizard/pricing
PUT /dashboard/api/wizard/pricing
GET /dashboard/api/wizard/partners
POST /dashboard/api/wizard/partners
DELETE /dashboard/api/wizard/partners/{partner_key}
GET /dashboard/api/shop/orders
PATCH /dashboard/api/shop/orders/{order_pk}
DELETE /dashboard/api/shop/orders/{order_pk}
GET /dashboard/api/users
POST /dashboard/api/users
PUT /dashboard/api/users/{user_id}
DELETE /dashboard/api/users/{user_id}
POST /dashboard/api/users/cleanup
POST /dashboard/api/audit-logs/retention
GET /dashboard/api/audit-logs/retention-config
""")

# Routes phase 2 has yet to scope; each PR of the phase takes its group off
NOT_YET_SCOPED: Set[Route] = _routes("""
GET /dashboard/api/sessions
GET /dashboard/api/sessions/detail
DELETE /dashboard/api/sessions
GET /dashboard/api/stats
GET /dashboard/api/usage/logs
GET /dashboard/api/rate-limits
GET /dashboard/api/rate-limits/usage
POST /dashboard/api/rate-limits
DELETE /dashboard/api/rate-limits/{scope}/{scope_id:path}
GET /dashboard/api/traces
GET /dashboard/api/traces/sessions
GET /dashboard/api/traces/replay
POST /dashboard/api/feedback
GET /dashboard/api/feedback
GET /dashboard/api/tokens
POST /dashboard/api/tokens
DELETE /dashboard/api/tokens/{token_id}
PUT /dashboard/api/agents/{config_id}/expose
GET /dashboard/api/agents
GET /dashboard/api/agents/export
POST /dashboard/api/agents/build-binary
GET /dashboard/api/agents/build-binary/{build_id}/status
GET /dashboard/api/agents/build-binary/{build_id}/download
POST /dashboard/api/agents/{agent_name}/clone
POST /dashboard/api/agents/import
GET /dashboard/api/templates
GET /dashboard/api/templates/{template_id}
DELETE /dashboard/api/templates/{template_id}
POST /dashboard/api/templates/import
POST /dashboard/api/templates/create-from-agents
GET /dashboard/api/templates/sync-status/{project_id}
POST /dashboard/api/templates/sync
POST /dashboard/api/agents
PUT /dashboard/api/agents/{config_id}
DELETE /dashboard/api/agents/{config_id}
POST /dashboard/api/agents/{agent_name}/create-folder
DELETE /dashboard/api/agents/{agent_name}/delete-folder
POST /dashboard/api/agents/{agent_name}/reinitialize
GET /dashboard/api/agents/{config_id}/versions
POST /dashboard/api/agents/{config_id}/rollback/{version_id}
PUT /dashboard/api/agents/versions/{version_id}/tag
GET /dashboard/api/agents/{agent_name}/file-search/config
GET /dashboard/api/agents/{agent_name}/file-search/stores
GET /dashboard/api/agents/{agent_name}/file-search/files
POST /dashboard/api/agents/{agent_name}/file-search/stores/assign
POST /dashboard/api/agents/{agent_name}/file-search/stores/unassign
POST /dashboard/api/file-search/stores/create
POST /dashboard/api/file-search/stores/upload
GET /dashboard/api/file-search/stores/{store_name}/files
GET /dashboard/api/agents/{agent_name}/memory-blocks
GET /dashboard/api/agents/{agent_name}/memory-blocks/{block_id}
POST /dashboard/api/agents/{agent_name}/memory-blocks
PUT /dashboard/api/agents/{agent_name}/memory-blocks/{block_id}
DELETE /dashboard/api/agents/{agent_name}/memory-blocks/{block_id}
GET /dashboard/api/agents/{agent_name}/memory-blocks/{block_id}/versions
GET /dashboard/api/agents/{agent_name}/memory-blocks-deleted
POST /dashboard/api/agents/{agent_name}/memory-block-versions/{version_id}/restore
GET /dashboard/api/file-search/stores/agents
GET /dashboard/api/projects/{project_id}/file-search/stores
POST /dashboard/api/file-search/stores/files/delete
POST /dashboard/api/file-search/stores/delete
POST /dashboard/api/agents/reinitialize-all
GET /dashboard/api/guardrail-logs
GET /dashboard/api/audit-logs
GET /dashboard/api/audit-logs/export
GET /dashboard/api/evals/agent/{agent_name}/versions-list
GET /dashboard/api/evals
GET /dashboard/api/evals/agent/{agent_name}/history
GET /dashboard/api/evals/agent/{agent_name}
GET /dashboard/api/evals/feedback
POST /dashboard/api/evals/improve/propose
POST /dashboard/api/evals/improve/check
GET /dashboard/api/evals/playground/agents
GET /dashboard/api/evals/playground/agent/{agent_name}
POST /dashboard/api/evals/playground/run
POST /dashboard/api/evals/improve/apply
POST /dashboard/api/evals
PUT /dashboard/api/evals/{test_case_id}
DELETE /dashboard/api/evals/{test_case_id}
POST /dashboard/api/evals/version/{version_id}/run
POST /dashboard/api/evals/{test_case_id}/run
POST /dashboard/api/workroom/title
GET /dashboard/api/triggers
POST /dashboard/api/triggers
PUT /dashboard/api/triggers/{trigger_id}
DELETE /dashboard/api/triggers/{trigger_id}
POST /dashboard/api/triggers/{trigger_id}/toggle
POST /dashboard/api/triggers/{trigger_id}/test-fire
GET /dashboard/api/alert-rules
POST /dashboard/api/alert-rules
PUT /dashboard/api/alert-rules/{rule_id}
DELETE /dashboard/api/alert-rules/{rule_id}
POST /dashboard/api/alert-rules/{rule_id}/test
GET /dashboard/api/widget-keys
POST /dashboard/api/widget-keys
PUT /dashboard/api/widget-keys/{key_id}
DELETE /dashboard/api/widget-keys/{key_id}
GET /dashboard/api/widget-keys/{key_id}/embed-code
GET /dashboard/api/channel-integrations
POST /dashboard/api/channel-integrations
PUT /dashboard/api/channel-integrations/{integration_id}
DELETE /dashboard/api/channel-integrations/{integration_id}
""")

# Writes that name no row in their path, and the test of this module that covers each
COVERED_BY_TEST: Dict[Route, str] = {
    ("POST", "/dashboard/api/projects"): "test_a_project_is_created_in_the_current_workspace",
}

# A row of workspace B for each path parameter of a scoped route
B_IDS: Dict[str, str] = {}


def _api_routes() -> List[APIRoute]:
    return [ctx.original_route for ctx in iter_route_contexts(auth_server.app.routes)
            if ctx.path and ctx.path.startswith(PROTECTED_PREFIX)
            and isinstance(ctx.original_route, APIRoute)]


def _pairs(route: APIRoute) -> List[Route]:
    return [(method, route.path) for method in sorted(route.methods)]


def _platform_admin(workspace_id: int = A) -> dict:
    credentials = base64.b64encode(f"{auth.AUTH_USERNAME}:{auth.AUTH_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {credentials}", WORKSPACE_HEADER: str(workspace_id)}


def _body(route: APIRoute) -> dict:
    """A body that passes the route's form validation, so the request reaches its handler."""
    fields = route.dependant.body_params
    if any(isinstance(f.field_info, params.Form) for f in fields):
        return {"data": {f.alias: "1" for f in fields}}
    return {"json": {}}


class _TwoWorkspaces(unittest.TestCase):
    """The real auth server on an in-memory database holding workspaces A and B."""

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.Session = sessionmaker(bind=engine)
        db = MagicMock()
        db.is_connected.return_value = True
        db.get_session.side_effect = lambda: self.Session()

        session = self.Session()
        session.add(Workspace(id=B, name=f"{B_MARKER} workspace", slug=B_MARKER))
        session.flush()
        for workspace_id, prefix in ((A, "ws-a"), (B, B_MARKER)):
            project = Project(name=f"{prefix}-project", workspace_id=workspace_id)
            session.add(project)
            session.flush()
            agent = prefix.replace("-", "_")
            session.add(AgentConfig(name=f"{agent}_root", type="llm", model_name="m",
                                    project_id=project.id, disabled=False, expose_as_model=True,
                                    allowed_for_roles='["user", "widget"]'))
            session.add(AgentConfig(name=f"{agent}_sub", type="llm", model_name="m",
                                    project_id=project.id, parent_agents=f'["{agent}_root"]',
                                    disabled=False))
        session.add(User(user_id=ALICE, roles='["user", "developer"]'))
        # bob is created for B, as the platform creates a user for an agent of B
        bob = User(user_id=BOB, roles='["user", "developer"]')
        bob.home_workspace_id = B
        session.add(bob)
        session.flush()
        session.query(WorkspaceMember).filter_by(user_id=BOB).update({"role": "owner"})
        for user_id, workspace_id in ((ALICE, A), (BOB, B)):
            session.add(PersonalAccessToken(token_hash=verify_pat_hash(PATS[user_id]),
                                            token_prefix=PATS[user_id][:16], name="t",
                                            user_id=user_id, workspace_id=workspace_id))
        self.ids = {
            "project_a": session.query(Project.id).filter_by(workspace_id=A).scalar(),
            "project_b": session.query(Project.id).filter_by(workspace_id=B).scalar(),
        }
        widget_key = WidgetApiKey(api_key="wk_a", project_id=self.ids["project_a"], agent_name=A_ROOT)
        session.add(widget_key)
        session.commit()
        self.ids["widget_key_a"] = widget_key.id
        session.close()

        dashboard = auth_server.dashboard_server
        for patcher in (
            patch.object(database_client, "_database_client", db),
            patch.object(dashboard, "db_client", db),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(auth_server.app)

    def b_ids(self) -> Dict[str, str]:
        return {**B_IDS, "project_id": str(self.ids["project_b"])}


class TestEveryRoute(_TwoWorkspaces):

    def test_every_route_is_classified_or_checked(self):
        # Both lists name only routes the app serves
        served = {pair for route in _api_routes() for pair in _pairs(route)}
        for name, routes in (("PLATFORM_ONLY", PLATFORM_ONLY), ("NOT_YET_SCOPED", NOT_YET_SCOPED),
                             ("COVERED_BY_TEST", set(COVERED_BY_TEST))):
            self.assertFalse(routes - served, f"{name} names routes the app does not serve")
        for test_name in COVERED_BY_TEST.values():
            self.assertTrue(hasattr(TestProjects, test_name), test_name)

    def test_no_get_route_shows_workspace_b(self):
        leaks = []
        for route in _api_routes():
            if "GET" not in route.methods or ("GET", route.path) in PLATFORM_ONLY | NOT_YET_SCOPED:
                continue
            if route.param_convertors:
                continue  # called with B's ids below
            response = self.client.get(route.path, headers=_platform_admin(A))
            if response.status_code >= 500 or B_MARKER in response.text:
                leaks.append(f"GET {route.path} -> {response.status_code}")
        self.assertFalse(leaks, "These routes answer workspace A with workspace B's data:\n"
                         + "\n".join(leaks))

    def test_every_route_naming_a_row_of_b_is_a_404(self):
        b_ids = self.b_ids()
        failures = []
        for route in _api_routes():
            for method, path in _pairs(route):
                if (method, path) in PLATFORM_ONLY | NOT_YET_SCOPED or not route.param_convertors:
                    continue
                missing = set(route.param_convertors) - set(b_ids)
                if missing:
                    failures.append(f"{method} {path}: no id of B for {sorted(missing)}; add it to B_IDS")
                    continue
                concrete = path
                for name in route.param_convertors:
                    concrete = concrete.replace("{" + name + "}", b_ids[name])
                kwargs = _body(route) if method in WRITE_METHODS else {}
                response = self.client.request(method, concrete, headers=_platform_admin(A), **kwargs)
                if response.status_code != 404:
                    failures.append(f"{method} {path} -> {response.status_code}")
        self.assertFalse(failures, "Called with workspace B's ids from workspace A, these routes "
                         "did not answer 404:\n" + "\n".join(failures))

    def test_every_write_naming_no_row_is_covered(self):
        uncovered = sorted(
            f"{method} {path}"
            for route in _api_routes() if not route.param_convertors
            for method, path in _pairs(route)
            if method in WRITE_METHODS
            and (method, path) not in PLATFORM_ONLY | NOT_YET_SCOPED | set(COVERED_BY_TEST)
        )
        self.assertFalse(uncovered, "Add a test that these writes act in the caller's workspace, "
                         "and name it in COVERED_BY_TEST:\n" + "\n".join(uncovered))


class TestProjects(_TwoWorkspaces):

    def _names(self, workspace_id: int) -> List[str]:
        response = self.client.get("/dashboard/api/projects", headers=_platform_admin(workspace_id))
        self.assertEqual(response.status_code, 200)
        return [p["name"] for p in response.json()["projects"]]

    def test_the_list_holds_the_current_workspace_only(self):
        self.assertEqual(self._names(A), ["ws-a-project"])
        self.assertEqual(self._names(B), [f"{B_MARKER}-project"])

    def test_a_project_is_created_in_the_current_workspace(self):
        with patch("shared.utils.audit_service.log"):
            response = self.client.post("/dashboard/api/projects", data={"name": "new"},
                                        headers=_platform_admin(B))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project"]["workspace_id"], B)

    def test_deleting_a_project_audits_its_workspace(self):
        project_b = self.ids["project_b"]
        response = self.client.delete(f"/dashboard/api/projects/{project_b}",
                                      headers=_platform_admin(B))
        self.assertEqual(response.status_code, 200)
        session = self.Session()
        entry = session.query(AuditLog).filter_by(action="project.delete").one()
        session.close()
        self.assertEqual((entry.resource_id, entry.workspace_id), (str(project_b), B))

    def test_another_workspace_s_project_is_left_alone(self):
        project_b = self.ids["project_b"]
        self.client.put(f"/dashboard/api/projects/{project_b}", data={"name": "renamed"},
                        headers=_platform_admin(A))
        self.client.delete(f"/dashboard/api/projects/{project_b}", headers=_platform_admin(A))
        self.assertEqual(self._names(B), [f"{B_MARKER}-project"])


class TestResolveWorkspace(_TwoWorkspaces):
    """Which workspace a request works in (server/request_context.py)."""

    def _resolve(self, user_id: str, platform_admin: bool, header: str = None):
        headers = [(WORKSPACE_HEADER.lower().encode(), header.encode())] if header is not None else []
        request = Request({"type": "http", "headers": headers})
        return resolve_workspace(request, user_id, platform_admin)

    def test_a_platform_admin_defaults_to_the_default_workspace(self):
        self.assertEqual(self._resolve(auth.AUTH_USERNAME, True)[0], A)

    def test_a_platform_admin_opens_any_workspace(self):
        self.assertEqual(self._resolve(auth.AUTH_USERNAME, True, str(B)), (B, None))

    def test_a_member_defaults_to_their_first_membership(self):
        self.assertEqual(self._resolve("bob@b.example", False), (B, "owner"))
        self.assertEqual(self._resolve("alice@a.example", False), (A, "member"))

    def test_another_workspace_is_a_404(self):
        for user_id, header in (("bob@b.example", str(A)), ("alice@a.example", str(B))):
            with self.subTest(user_id=user_id), self.assertRaises(HTTPException) as caught:
                self._resolve(user_id, False, header)
            self.assertEqual(caught.exception.status_code, 404)

    def test_an_unknown_or_deleted_workspace_is_a_404(self):
        session = self.Session()
        session.query(Workspace).filter_by(id=B).update({"deleted_at": Workspace.created_at})
        session.commit()
        session.close()
        for header in ("99", "abc", "", str(B)):
            with self.subTest(header=header), self.assertRaises(HTTPException) as caught:
                self._resolve(auth.AUTH_USERNAME, True, header)
            self.assertEqual(caught.exception.status_code, 404)


class TestRbacCheck(_TwoWorkspaces):
    """RBACMiddleware.check_agent_access, which the RBAC callback, LangGraph and MCP use."""

    def _allowed(self, user_id: str, agent: str, roles=("user", "widget")) -> bool:
        rbac = RBACMiddleware.__new__(RBACMiddleware)
        rbac.user_service = UserService()
        return rbac.check_agent_access(user_id, {"name": agent, "allowed_for_roles": list(roles)})[0]

    def _memberships(self, user_id: str) -> Dict[int, str]:
        session = self.Session()
        try:
            return dict(session.query(WorkspaceMember.workspace_id, WorkspaceMember.role)
                        .filter_by(user_id=user_id).all())
        finally:
            session.close()

    def test_a_member_runs_only_their_workspace_s_agents(self):
        self.assertTrue(self._allowed(ALICE, A_ROOT))
        self.assertFalse(self._allowed(ALICE, B_ROOT))

    def test_an_owner_passes_the_agent_s_roles_in_their_workspace_only(self):
        self.assertTrue(self._allowed(BOB, B_ROOT, roles=["finance"]))
        self.assertFalse(self._allowed(BOB, A_ROOT))

    def test_a_platform_administrator_runs_every_agent(self):
        self.assertTrue(self._allowed(auth.AUTH_USERNAME, B_ROOT, roles=["finance"]))

    def test_a_new_user_joins_the_workspace_of_the_agent(self):
        # An end user an API client runs through /run_sse, an A2A or a Slack user
        self.assertTrue(self._allowed("end-user-of-b", B_ROOT))
        self.assertEqual(self._memberships("end-user-of-b"), {B: "member"})
        session = self.Session()
        sync_default_members(session)
        session.close()
        self.assertEqual(self._memberships("end-user-of-b"), {B: "member"})
        # Once a member of B, the same id doesn't reach A
        self.assertFalse(self._allowed("end-user-of-b", A_ROOT))

    def test_a_new_user_of_the_default_workspace_joins_it(self):
        self.assertTrue(self._allowed("end-user-of-a", A_ROOT))
        self.assertEqual(self._memberships("end-user-of-a"), {A: "member"})

    def test_a_widget_visitor_reaches_their_key_s_workspace_only(self):
        visitor = f"widget_{self.ids['widget_key_a']}_v1"
        self.assertTrue(self._allowed(visitor, A_ROOT))
        self.assertFalse(self._allowed(visitor, B_ROOT))
        self.assertEqual(self._memberships(visitor), {})

    def test_an_agent_without_a_config_is_for_platform_administrators(self):
        self.assertFalse(self._allowed(ALICE, "not_configured"))
        self.assertTrue(self._allowed(auth.AUTH_USERNAME, "not_configured"))


class TestOpenAIApiAndMcp(_TwoWorkspaces):
    """A personal access token works in its own workspace."""

    def _models(self, user_id: str) -> List[str]:
        response = self.client.get("/v1/models", headers={"Authorization": f"Bearer {PATS[user_id]}"})
        self.assertEqual(response.status_code, 200, response.text)
        return [m["id"] for m in response.json()["data"]]

    def test_the_models_are_the_token_s_workspace_s(self):
        self.assertEqual(self._models(ALICE), [A_ROOT])
        self.assertEqual(self._models(BOB), [B_ROOT])

    def test_another_workspace_s_model_is_a_404(self):
        with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}):
            response = self.client.post(
                "/v1/chat/completions", headers={"Authorization": f"Bearer {PATS[ALICE]}"},
                json={"model": B_ROOT, "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 404)

    def test_a_token_whose_owner_left_its_workspace_is_refused(self):
        session = self.Session()
        session.query(WorkspaceMember).filter_by(user_id=BOB).delete()
        session.commit()
        session.close()
        response = self.client.get("/v1/models", headers={"Authorization": f"Bearer {PATS[BOB]}"})
        self.assertEqual(response.status_code, 403)

    def test_mcp_allows_the_token_s_workspace_s_agents_only(self):
        self.assertTrue(agent_allowed(ALICE, A_ROOT, A))
        self.assertFalse(agent_allowed(ALICE, B_ROOT, A))
        # Not even an owner of B, with a token of A
        self.assertFalse(agent_allowed(BOB, B_ROOT, A))


class TestProxy(_TwoWorkspaces):
    """/list-apps and the agent routes, with a fake agent server."""

    APPS = [A_ROOT, B_ROOT, "not_configured"]

    def setUp(self):
        super().setUp()
        self.reached = []

        def agent_server(request: httpx.Request) -> httpx.Response:
            self.reached.append(request.url.path)
            if request.url.path == "/list-apps":
                if request.url.params.get("detailed") == "true":
                    return httpx.Response(200, json={"apps": [{"name": n} for n in self.APPS]})
                return httpx.Response(200, json=self.APPS)
            return httpx.Response(200, json={"id": "s1"})

        transport = httpx.MockTransport(agent_server)
        real_client = httpx.AsyncClient
        patcher = patch.object(httpx, "AsyncClient",
                               lambda *a, **kw: real_client(transport=transport, timeout=kw.get("timeout")))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _as_member(self, user_id: str) -> None:
        auth_server.app.dependency_overrides[proxy_routes.get_auth_user] = lambda: user_id
        self.addCleanup(auth_server.app.dependency_overrides.pop, proxy_routes.get_auth_user, None)
        for patcher in (patch("server.auth.is_admin_user", return_value=False),
                        patch("server.request_context.get_user_role", return_value="user")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_list_apps_holds_the_request_s_workspace(self):
        def apps(workspace_id: int, query: str = "") -> list:
            response = self.client.get(f"/list-apps{query}", headers=_platform_admin(workspace_id))
            self.assertEqual(response.status_code, 200)
            body = response.json()
            return body if isinstance(body, list) else [a["name"] for a in body["apps"]]

        self.assertEqual(apps(A), [A_ROOT, "not_configured"])
        self.assertEqual(apps(B), [B_ROOT])
        self.assertEqual(apps(A, "?detailed=true"), [A_ROOT, "not_configured"])

    def test_a_member_lists_their_workspace_s_apps(self):
        self._as_member(BOB)
        self.assertEqual(self.client.get("/list-apps").json(), [B_ROOT])

    def test_a_member_cannot_reach_another_workspace_s_agent(self):
        self._as_member(ALICE)
        for method, path, body in (
            ("POST", f"/apps/{B_ROOT}/users/{ALICE}/sessions", {}),
            ("GET", f"/apps/{B_ROOT}/users/{ALICE}/sessions", None),
            ("POST", "/run_sse", {"app_name": B_ROOT, "user_id": ALICE, "session_id": "s",
                                  "new_message": {"parts": [{"text": "hi"}]}}),
        ):
            with self.subTest(path=path):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reached, [])

    def test_a_member_reaches_their_own_workspace_s_agent(self):
        self._as_member(ALICE)
        response = self.client.post(f"/apps/{A_ROOT}/users/{ALICE}/sessions", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reached, [f"/apps/{A_ROOT}/users/{ALICE}/sessions"])


class TestWorkRoom(_TwoWorkspaces):

    def _agents(self, user_id: str, role: str, roles=None) -> str:
        for patcher in (patch("server.request_context.require_dashboard_auth", return_value=user_id),
                        patch("server.request_context.get_user_role", return_value=role)):
            patcher.start()
            self.addCleanup(patcher.stop)
        response = self.client.get("/dashboard/workroom")
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_a_member_sees_their_workspace_s_root_agents(self):
        page = self._agents(ALICE, "user")
        self.assertIn(A_ROOT, page)
        self.assertNotIn(B_ROOT, page)

    def test_an_agent_the_member_may_not_use_is_left_out(self):
        session = self.Session()
        session.query(AgentConfig).filter_by(name=A_ROOT).update({"allowed_for_roles": '["finance"]'})
        session.commit()
        session.close()
        self.assertNotIn(A_ROOT, self._agents(ALICE, "user"))


if __name__ == "__main__":
    unittest.main()
