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
added. PLATFORM_ONLY lists the routes that act on the whole installation; they stay
for platform administrators, and an owner of a workspace is refused every one.
SHARED lists the routes that read what every workspace shares, the template
catalogue, and ABOUT_THE_CALLER the routes about the caller rather than a workspace.

The agent runtime is checked the same way: the RBAC check, the proxy and
/list-apps, the OpenAI API, MCP and the Work Room, called with A's credentials on
B's agents.
"""

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, params
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
import httpx
import itsdangerous
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# auth_server sets OTEL_SDK_DISABLED on import; keep that out of other tests.
with patch.dict(os.environ):
    import auth_server
from server import auth, proxy_routes
from server.dashboard_authz import PROTECTED_PREFIX, WRITE_METHODS, is_platform_only
from server.request_context import SESSION_WORKSPACE_KEY, WORKSPACE_HEADER, resolve_workspace
from server.pat_auth import verify_pat_hash
from shared.utils import (
    alert_service, database_client, feedback_service, guardrail_log_service, rate_limit_service,
    token_usage_service, user_service,
)
from shared.utils.mcp.mcp_auth import agent_allowed
from shared.utils.models import (
    AgentConfig, AgentConfigVersion, AgentResponse, AgentTrigger, AlertRule, AuditLog, Base,
    ChannelIntegration, FileSearchStore, GuardrailLog, LangGraphSession, MemoryBlock,
    PersonalAccessToken, Project, RateLimitConfig, ResponseFeedback, TestCase, TokenUsageLog, User,
    WidgetApiKey, Workspace, WorkspaceMember,
)
from shared.utils.rbac_middleware import RBACMiddleware
from shared.utils.tools import mcp_tools
from shared.utils.tools.tool_factory import ToolFactory
from shared.utils.user_service import UserService
from shared.utils.workspaces import sync_default_members

B_MARKER = "zz-ws-b"
# Agent names can't hold a hyphen, so B's carry the marker with underscores
B_MARKERS = (B_MARKER, B_MARKER.replace("-", "_"))
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
DELETE /dashboard/api/templates/{template_id}
POST /dashboard/api/templates/create-from-agents
POST /dashboard/api/agents/reinitialize-all
POST /dashboard/api/agents/build-binary
GET /dashboard/api/agents/build-binary/{build_id}/status
GET /dashboard/api/agents/build-binary/{build_id}/download
""")

# The template catalogue: files every workspace imports from. Writing to it is
# platform-only above, so it holds no workspace's agents.
SHARED: Set[Route] = _routes("""
GET /dashboard/api/templates/{template_id}
""")

# About the caller, not a workspace: the workspaces they may open, which for a platform
# administrator are all of them (test_the_switcher_lists_the_caller_s_workspaces)
ABOUT_THE_CALLER: Set[Route] = _routes("""
GET /dashboard/api/workspaces
""")

# Writes that name a row in their body, each with a body that names one of workspace
# B's; called from workspace A, every one must answer 404. Values are format strings
# over _TwoWorkspaces.b_ids(): {"json": ...}, {"data": ...} or {"data": ..., "files": ...}.
BODIES_NAMING_B: Dict[Route, dict] = {
    ("POST", "/dashboard/api/agents"): {"data": {
        "name": "new_agent", "type": "llm", "project_id": "{project_id}",
        "description": "d", "instruction": "i"}},
    ("POST", "/dashboard/api/agents/build-binary"): {"json": {"project_id": "{project_id}"}},
    ("POST", "/dashboard/api/templates/sync"): {"json": {"project_id": "{project_id}"}},
    ("POST", "/dashboard/api/file-search/stores/create"): {"json": {
        "display_name": "s", "project_id": "{project_id}"}},
    ("POST", "/dashboard/api/file-search/stores/upload"): {
        "data": {"store_name": "{store_name}"}, "files": {"file": ("a.txt", b"a")}},
    ("POST", "/dashboard/api/file-search/stores/files/delete"): {"data": {
        "store_name": "{store_name}", "document_name": "d"}},
    ("POST", "/dashboard/api/file-search/stores/delete"): {"data": {"store_name": "{store_name}"}},
    ("POST", "/dashboard/api/evals"): {"json": {
        "agent_name": "{agent_name}", "input": "q", "expected_output": "a"}},
    ("POST", "/dashboard/api/evals/improve/propose"): {"json": {"test_case_id": "{test_case_id}"}},
    ("POST", "/dashboard/api/evals/improve/check"): {"json": {
        "agent_name": "{agent_name}", "instruction": "i"}},
    ("POST", "/dashboard/api/evals/improve/apply"): {"json": {
        "agent_name": "{agent_name}", "instruction": "i", "base_instruction": ""}},
    ("POST", "/dashboard/api/evals/playground/run"): {"json": {
        "agent_name": "{agent_name}", "mode": "prompt", "prompt": "p", "variants": [{}, {}]}},
    ("POST", "/dashboard/api/workroom/title"): {"json": {
        "agent": "{agent_name}", "user_id": "{admin}", "session_id": "s"}},
    ("DELETE", "/dashboard/api/sessions"): {"params": {
        "session_id": "{lg_session_id}", "app_name": "{agent_name}", "user_id": "{bob}",
        "runtime": "langgraph"}},
    ("POST", "/dashboard/api/rate-limits"): {"json": {
        "scope": "agent", "scope_id": "{agent_name}", "tokens_per_day": 1}},
    ("POST", "/dashboard/api/alert-rules"): {"json": {
        "name": "r", "scope": "agent", "scope_id": "{agent_name}",
        "condition_type": "agent_error_count", "destination_type": "http",
        "destination_config": {"url": "https://example.com/hook"}}},
    ("POST", "/dashboard/api/triggers"): {"json": {
        "name": "t", "agent_name": "{agent_name}", "project_id": "{project_id}",
        "trigger_type": "webhook", "prompt": "p"}},
    ("POST", "/dashboard/api/feedback"): {"json": {
        "session_id": "s", "message_id": "m2", "rating": "up", "agent_name": "{agent_name}"}},
    ("POST", "/dashboard/api/widget-keys"): {"json": {
        "project_id": "{project_id}", "agent_name": "{agent_name}"}},
    ("POST", "/dashboard/api/channel-integrations"): {"json": {
        "project_id": "{project_id}", "agent_name": "{agent_name}"}},
}

# Writes that name no row, and the test of this module that covers each
COVERED_BY_TEST: Dict[Route, str] = {
    ("POST", "/dashboard/api/projects"): "test_a_project_is_created_in_the_current_workspace",
    ("POST", "/dashboard/api/agents/import"): "test_an_import_keeps_to_the_current_workspace",
    ("POST", "/dashboard/api/templates/import"): "test_a_template_is_imported_into_the_current_workspace",
    ("POST", "/dashboard/api/tokens"): "test_a_token_acts_in_the_current_workspace",
    ("PUT", "/dashboard/api/workspaces/current"): "test_the_session_works_in_the_workspace_it_switched_to",
    **{route: "test_every_body_naming_a_row_of_b_is_a_404" for route in BODIES_NAMING_B},
}

# A row of workspace B for each path parameter of a scoped route, set by the fixture
B_IDS: Dict[str, str] = {"agent_name": B_ROOT, "build_id": "build-of-b",
                         "scope": "agent", "scope_id": B_ROOT}


def _api_routes() -> List[APIRoute]:
    return [ctx.original_route for ctx in iter_route_contexts(auth_server.app.routes)
            if ctx.path and ctx.path.startswith(PROTECTED_PREFIX)
            and isinstance(ctx.original_route, APIRoute)]


def _pairs(route: APIRoute) -> List[Route]:
    return [(method, route.path) for method in sorted(route.methods)]


def _platform_admin(workspace_id: int = A) -> dict:
    credentials = base64.b64encode(f"{auth.AUTH_USERNAME}:{auth.AUTH_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {credentials}", WORKSPACE_HEADER: str(workspace_id)}


def _signed_in(user_id: str, workspace_id: int = None) -> Dict[str, str]:
    """The session cookie of a single sign-on user, as the OAuth callback writes it, and
    the workspace the session switched to."""
    data = {"user": {"provider": "google", "user_id": user_id, "email": user_id}}
    if workspace_id is not None:
        data[SESSION_WORKSPACE_KEY] = workspace_id
    signer = itsdangerous.TimestampSigner(str(auth_server._SECRET_KEY))
    return {"session": signer.sign(base64.b64encode(json.dumps(data).encode())).decode()}


def _fill(value, ids: Dict[str, str]):
    """BODIES_NAMING_B's value with B's ids put in; an id alone stays an int."""
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    if isinstance(value, str) and value.startswith("{") and value.endswith("}") \
            and value[1:-1] in ids:
        filled = ids[value[1:-1]]
        return int(filled) if filled.isdigit() else filled
    return value


def _test_names() -> Set[str]:
    return {name for cls in globals().values()
            if isinstance(cls, type) and issubclass(cls, unittest.TestCase) for name in dir(cls)}


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
        # B's agent, its config history, memory, file search store, eval case and rating
        b_root = session.query(AgentConfig).filter_by(name=B_ROOT).one()
        version = AgentConfigVersion(agent_config_id=b_root.id, version_number=1,
                                     config_snapshot=json.dumps({"name": B_ROOT}))
        block = MemoryBlock(project_id=self.ids["project_b"], label=f"{B_MARKER}-block", value=B_MARKER)
        store = FileSearchStore(store_name=f"{B_MARKER}-store", display_name=B_MARKER,
                                project_id=self.ids["project_b"])
        test_case = TestCase(agent_name=B_ROOT, input=B_MARKER, expected_output=B_MARKER,
                             workspace_id=B)
        session.add_all([version, block, store, test_case,
                         ResponseFeedback(session_id="s", message_id="m", agent_name=B_ROOT,
                                          project_id=self.ids["project_b"], rating="down",
                                          question=B_MARKER, answer=B_MARKER)])
        # B's logs, as its agent writes them, and its operations
        session.add_all([
            TokenUsageLog(request_id=f"{B_MARKER}-request", session_id=f"{B_MARKER}-session",
                          user_id=BOB, agent_name=B_ROOT, model_name=B_MARKER, status="SUCCESS",
                          prompt_tokens=1, response_tokens=1, workspace_id=B),
            GuardrailLog(request_id=f"{B_MARKER}-request", agent_name=B_ROOT, guardrail_type=B_MARKER,
                         action_taken="block", workspace_id=B),
            AuditLog(actor=BOB, action=B_MARKER, resource_type="agent", resource_id=B_ROOT,
                     workspace_id=B),
            AgentResponse(invocation_id=f"{B_MARKER}-invocation", agent_name=B_ROOT, user_id=BOB,
                          started_at=datetime.now(timezone.utc).replace(tzinfo=None), workspace_id=B),
            LangGraphSession(id=f"{B_MARKER}-lg-session", app_name=B_ROOT, user_id=BOB),
        ])
        b_ops = {
            "trigger_id": AgentTrigger(name=f"{B_MARKER}-trigger", agent_name=B_ROOT,
                                       project_id=self.ids["project_b"], prompt=B_MARKER),
            "rule_id": AlertRule(name=f"{B_MARKER}-rule", scope="agent", scope_id=B_ROOT,
                                 condition_type="agent_error_count", destination_type="http",
                                 workspace_id=B),
            "key_id": WidgetApiKey(api_key="wk_b", project_id=self.ids["project_b"],
                                   agent_name=B_ROOT, label=B_MARKER),
            "integration_id": ChannelIntegration(project_id=self.ids["project_b"], agent_name=B_ROOT,
                                                 label=B_MARKER, team_id="T_B"),
            "rate_limit": RateLimitConfig(scope="agent", scope_id=B_ROOT, tokens_per_day=1,
                                          workspace_id=B),
        }
        session.add_all(b_ops.values())
        session.commit()
        self.ids["widget_key_a"] = widget_key.id
        self.b_rows = {"config_id": b_root.id, "version_id": version.id, "block_id": block.id,
                       "test_case_id": test_case.id, "store_name": store.store_name,
                       "token_id": session.query(PersonalAccessToken.id).filter_by(user_id=BOB).scalar(),
                       **{name: row.id for name, row in b_ops.items()}}
        session.close()

        dashboard = auth_server.dashboard_server
        for patcher in (
            patch.object(database_client, "_database_client", db),
            patch.object(dashboard, "db_client", db),
            # The services keep the database client they were made with
            patch.object(rate_limit_service, "_rate_limit_service", None),
            patch.object(alert_service, "_alert_service", None),
            patch.object(token_usage_service, "_token_usage_service", None),
            patch.object(guardrail_log_service, "_guardrail_log_service", None),
            patch.object(feedback_service, "_feedback_service", None),
            patch.object(user_service, "_user_service", None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(auth_server.app)

    def b_ids(self) -> Dict[str, str]:
        return {**B_IDS, **{k: str(v) for k, v in self.b_rows.items()},
                "project_id": str(self.ids["project_b"]), "admin": auth.AUTH_USERNAME,
                "bob": BOB, "lg_session_id": f"{B_MARKER}-lg-session"}


class TestEveryRoute(_TwoWorkspaces):

    def test_every_route_is_classified_or_checked(self):
        # Both lists name only routes the app serves
        served = {pair for route in _api_routes() for pair in _pairs(route)}
        for name, routes in (("PLATFORM_ONLY", PLATFORM_ONLY), ("SHARED", SHARED),
                             ("ABOUT_THE_CALLER", ABOUT_THE_CALLER),
                             ("COVERED_BY_TEST", set(COVERED_BY_TEST))):
            self.assertFalse(routes - served, f"{name} names routes the app does not serve")
        for test_name in COVERED_BY_TEST.values():
            self.assertIn(test_name, _test_names())

    def test_no_get_route_shows_workspace_b(self):
        leaks = []
        for route in _api_routes():
            if "GET" not in route.methods or ("GET", route.path) in PLATFORM_ONLY | ABOUT_THE_CALLER:
                continue
            if route.param_convertors:
                continue  # called with B's ids below
            response = self.client.get(route.path, headers=_platform_admin(A))
            if response.status_code >= 500 or any(m in response.text for m in B_MARKERS):
                leaks.append(f"GET {route.path} -> {response.status_code}")
        self.assertFalse(leaks, "These routes answer workspace A with workspace B's data:\n"
                         + "\n".join(leaks))

    def test_every_route_naming_a_row_of_b_is_a_404(self):
        b_ids = self.b_ids()
        failures = []
        for route in _api_routes():
            for method, path in _pairs(route):
                if (method, path) in PLATFORM_ONLY | SHARED or not route.param_convertors:
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

    def test_every_body_naming_a_row_of_b_is_a_404(self):
        b_ids = self.b_ids()
        failures = []
        for (method, path), body in BODIES_NAMING_B.items():
            response = self.client.request(method, path, headers=_platform_admin(A),
                                           **_fill(body, b_ids))
            if response.status_code != 404:
                failures.append(f"{method} {path} -> {response.status_code} {response.text[:200]}")
        self.assertFalse(failures, "Naming a row of workspace B in the body from workspace A, "
                         "these routes did not answer 404:\n" + "\n".join(failures))

    def test_every_write_naming_no_row_is_covered(self):
        uncovered = sorted(
            f"{method} {path}"
            for route in _api_routes() if not route.param_convertors
            for method, path in _pairs(route)
            if method in WRITE_METHODS
            and (method, path) not in PLATFORM_ONLY | set(COVERED_BY_TEST)
        )
        self.assertFalse(uncovered, "Add a test that these writes act in the caller's workspace, "
                         "and name it in COVERED_BY_TEST:\n" + "\n".join(uncovered))


class TestLogsAndOperations(_TwoWorkspaces):
    """Logs, traces, sessions, budgets, alert rules, keys and integrations (phase 2d)."""

    def _get(self, path: str, workspace_id: int, **params) -> httpx.Response:
        response = self.client.get(path, headers=_platform_admin(workspace_id), params=params)
        self.assertEqual(response.status_code, 200, f"{path}: {response.text[:200]}")
        return response

    def test_each_workspace_sees_its_own_rows(self):
        for path in ("/dashboard/api/usage/logs", "/dashboard/api/guardrail-logs",
                     "/dashboard/api/audit-logs", "/dashboard/api/sessions",
                     "/dashboard/api/rate-limits", "/dashboard/api/alert-rules",
                     "/dashboard/api/triggers", "/dashboard/api/widget-keys",
                     "/dashboard/api/channel-integrations"):
            self.assertIn(B_ROOT, self._get(path, B).text, path)
            self.assertNotIn(B_ROOT, self._get(path, A).text, path)
        stats = self._get("/dashboard/api/stats", B).json()
        self.assertEqual([row["agent"] for row in stats["top_agents"]], [B_ROOT])
        self.assertEqual(self._get("/dashboard/api/stats", A).json()["top_agents"], [])
        # The pages that render the same figures
        for page in ("/dashboard/usage?view=logs", "/dashboard/overview"):
            self.assertIn(B_ROOT, self._get(page, B).text, page)
            self.assertNotIn(B_ROOT, self._get(page, A).text, page)

    def test_another_workspace_s_session_is_left_alone(self):
        params = {"session_id": f"{B_MARKER}-lg-session", "app_name": B_ROOT, "user_id": BOB,
                  "runtime": "langgraph"}
        from shared.utils.langgraph import session_store
        with patch.object(session_store, "_session_store", None):
            response = self.client.get("/dashboard/api/sessions/detail", params=params,
                                       headers=_platform_admin(A))
            self.assertEqual(response.status_code, 404)
            self.client.delete("/dashboard/api/sessions", params=params, headers=_platform_admin(A))
            self._get("/dashboard/api/sessions/detail", B, **params)

    def test_traces_keep_to_their_workspace(self):
        session = self.Session()
        session.execute(text(
            "CREATE TABLE trace_spans (id INTEGER PRIMARY KEY, trace_id TEXT, span_id TEXT, "
            "parent_span_id TEXT, name TEXT, kind TEXT, start_time DATETIME, end_time DATETIME, "
            "duration_ms INTEGER, attributes TEXT, status TEXT, error_message TEXT)"))
        now = datetime.now()
        for trace, agent in (("trace-a", A_ROOT), ("trace-b", B_ROOT), ("trace-none", None)):
            attributes = {"gen_ai.operation.name": "invoke_agent",
                          "gen_ai.conversation.id": f"session-{trace}"}
            if agent:
                attributes["gen_ai.agent.name"] = agent
            session.execute(text(
                "INSERT INTO trace_spans (trace_id, span_id, name, start_time, end_time, attributes) "
                "VALUES (:t, 's', 'invoke_agent', :st, :st, :a)"),
                {"t": trace, "st": now, "a": json.dumps(attributes)})
        session.commit()
        session.close()

        def traces(workspace_id: int) -> List[str]:
            return [t["trace_id"] for t in self._get("/dashboard/api/traces", workspace_id).json()["traces"]]

        self.assertEqual(sorted(traces(A)), ["trace-a", "trace-none"])
        self.assertEqual(traces(B), ["trace-b"])
        sessions = self._get("/dashboard/api/traces/sessions", B).json()["sessions"]
        self.assertEqual([s["session_id"] for s in sessions], ["session-trace-b"])
        replay = "/dashboard/api/traces/replay"
        self.assertEqual(self._get(replay, A, session_id="session-trace-b").json()["turns"], [])
        self.assertTrue(self._get(replay, B, session_id="session-trace-b").json()["turns"])

    def test_a_budget_counts_a_user_in_its_own_workspace(self):
        import asyncio
        service = rate_limit_service.get_rate_limit_service()
        check = lambda agent: asyncio.run(service.check_request_limit(BOB, agent_name=agent))[0]  # noqa: E731
        service.delete_config("agent", B_ROOT, workspace_id=B)
        # bob's two tokens were used in B; a budget on him in A doesn't count them,
        # and doesn't apply in B
        service.upsert_config("user", BOB, workspace_id=A, tokens_per_day=1)
        self.assertTrue(check(A_ROOT).allowed)
        self.assertTrue(check(B_ROOT).allowed)
        service.upsert_config("user", BOB, workspace_id=B, tokens_per_day=1)
        self.assertFalse(check(B_ROOT).allowed)
        usage = "/dashboard/api/rate-limits/usage"
        self.assertEqual(self._get(usage, A, user_id=BOB).json()["tokens_last_day"], 0)
        self.assertEqual(self._get(usage, B, user_id=BOB).json()["tokens_last_day"], 2)
        self.assertEqual(self.client.get(usage, params={"agent_name": B_ROOT},
                                         headers=_platform_admin(A)).status_code, 404)

    def test_an_alert_rule_measures_its_own_workspace(self):
        session = self.Session()
        session.add(TokenUsageLog(request_id="r", agent_name=B_ROOT, status="ERROR", workspace_id=B))
        session.commit()
        session.close()
        service = alert_service.get_alert_service()
        values = {}
        for workspace_id in (A, B):
            rule = service.create_rule(name="all", scope="global", condition_type="agent_error_count",
                                       destination_type="http", workspace_id=workspace_id)
            values[workspace_id] = service.evaluate_rule(rule["id"])["value"]
        self.assertEqual(values, {A: 0, B: 1})

    def test_a_slack_team_connects_to_one_integration(self):
        response = self.client.post("/dashboard/api/channel-integrations", headers=_platform_admin(A),
                                    json={"project_id": self.ids["project_a"], "agent_name": A_ROOT,
                                          "team_id": "T_B"})
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (409, "This Slack team is already connected"))

    def test_a_token_acts_in_the_current_workspace(self):
        with patch("shared.utils.audit_service.log"):
            response = self.client.post("/dashboard/api/tokens", headers=_platform_admin(B),
                                        json={"name": "in b"})
        self.assertEqual(response.status_code, 200, response.text)
        session = self.Session()
        token = session.query(PersonalAccessToken).filter_by(name="in b").one()
        session.close()
        self.assertEqual(token.workspace_id, B)
        listed = lambda w: [t["name"] for t in self._get("/dashboard/api/tokens", w).json()["tokens"]]  # noqa: E731
        self.assertEqual(listed(B), ["in b"])
        self.assertEqual(listed(A), [])


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


class TestAgents(_TwoWorkspaces):
    """Agent writes, imports and the agent tree (D1)."""

    def _agent(self, name: str) -> AgentConfig:
        session = self.Session()
        try:
            return session.query(AgentConfig).filter_by(name=name).first()
        finally:
            session.close()

    def _create(self, name: str, parents: str = "[]") -> httpx.Response:
        with patch("shared.utils.audit_service.log"):
            return self.client.post("/dashboard/api/agents", headers=_platform_admin(A), data={
                "name": name, "type": "llm", "project_id": str(self.ids["project_a"]),
                "description": "d", "instruction": "i", "parent_agents": parents})

    def test_agent_names_are_checked_across_workspaces(self):
        response = self._create(B_ROOT)
        self.assertEqual((response.status_code, response.json()["detail"]), (409, "This name is taken"))
        response = self._create("new_agent", json.dumps([B_ROOT]))
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._agent("new_agent"))
        self.assertEqual(self._create("new_agent", json.dumps([A_ROOT])).status_code, 200)
        self.assertEqual(self._agent("new_agent").get_parent_agents(), [A_ROOT])

    def test_an_import_keeps_to_the_current_workspace(self):
        with patch("shared.utils.audit_service.log"):
            response = self.client.post(
                "/dashboard/api/agents/import?overwrite=true", headers=_platform_admin(A), json={
                    "agents": [
                        # B's agent, overwritten from A
                        {"name": B_ROOT, "type": "llm", "project_id": self.ids["project_a"],
                         "instruction": "taken"},
                        # into B's project
                        {"name": "into_b", "type": "llm", "project_id": self.ids["project_b"]},
                        # under B's agent
                        {"name": "under_b", "type": "llm", "project_id": self.ids["project_a"],
                         "parent_agents": [B_ROOT]},
                        {"name": "into_a", "type": "llm", "project_id": self.ids["project_a"]},
                    ],
                    "memory_blocks": [{"project_id": self.ids["project_b"], "label": "x", "value": "x"}],
                    "triggers": [{"name": "t", "agent_name": B_ROOT}],
                })
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["imported_count"], response.json()["skipped_count"]), (1, 3))
        self.assertIsNone(self._agent(B_ROOT).instruction)
        self.assertEqual(self._agent(B_ROOT).project_id, self.ids["project_b"])
        self.assertIsNone(self._agent("into_b"))
        self.assertIsNone(self._agent("under_b"))
        self.assertEqual(self._agent("into_a").project_id, self.ids["project_a"])
        session = self.Session()
        self.assertIsNone(session.query(MemoryBlock).filter_by(label="x").first())
        self.assertIsNone(session.query(AgentTrigger).filter_by(name="t").first())
        session.close()

    def test_a_template_is_imported_into_the_current_workspace(self):
        template = {"project": {"name": "from template"},
                    "template_meta": {"agent_prefix": "tpl_", "root_agent": "tpl_root"},
                    "agents": [{"name": "tpl_root", "type": "llm"}]}
        dashboard = auth_server.dashboard_server
        with patch.object(dashboard.template_service, "get_template", return_value=template), \
                patch.object(dashboard, "_copy_template_agent"), patch("shared.utils.audit_service.log"):
            response = self.client.post("/dashboard/api/templates/import", headers=_platform_admin(B),
                                        json={"template_id": "t"})
        self.assertEqual(response.status_code, 200, response.text)
        session = self.Session()
        project = session.get(Project, response.json()["project_id"])
        self.assertEqual(project.workspace_id, B)
        session.close()
        self.assertEqual(self._agent(response.json()["root_agent_name"]).project_id, project.id)

    def test_the_agent_tree_keeps_to_a_workspace(self):
        from shared.utils.agent_manager import AgentManager
        # A sub-agent of B that names A's root, as no save allows any more
        session = self.Session()
        session.query(AgentConfig).filter_by(name="zz_ws_b_sub").update(
            {"parent_agents": json.dumps([B_ROOT, A_ROOT])})
        session.commit()
        session.close()
        manager = AgentManager.__new__(AgentManager)
        manager.get_session = self.Session
        self.assertEqual([a.name for a in manager.get_subagents(A_ROOT)], ["ws_a_sub"])
        self.assertEqual([a.name for a in manager.get_subagents(B_ROOT)], ["zz_ws_b_sub"])
        from shared.utils.langgraph.agent_builder import _load_child_configs
        self.assertEqual([a["name"] for a in _load_child_configs(A_ROOT)], ["ws_a_sub"])

    def test_the_agent_tools_keep_to_the_calling_agent_s_workspace(self):
        from types import SimpleNamespace
        from shared.utils.tools import create_agent_tool as tools
        context = SimpleNamespace(agent_name=A_ROOT)
        with patch.object(tools, "_is_admin_user", return_value=True), \
                patch.object(tools, "reload_agent_cache", return_value={"success": True}):
            self.assertEqual(tools.update_agent(B_ROOT, instruction="x", tool_context=context)["status"],
                             "error")
            self.assertEqual(tools.update_agent("ws_a_sub", parent_agent=B_ROOT,
                                                tool_context=context)["status"], "error")
            self.assertEqual(tools.delete_agent(B_ROOT, tool_context=context)["status"], "error")
        self.assertIsNone(self._agent(B_ROOT).instruction)
        self.assertEqual(self._agent("ws_a_sub").get_parent_agents(), [A_ROOT])

    def test_the_lists_hold_the_current_workspace(self):
        for path, key in (("/dashboard/api/agents", "configs"), ("/dashboard/api/evals", "suites"),
                          ("/dashboard/api/evals/playground/agents", "agents")):
            with self.subTest(path=path):
                response = self.client.get(path, headers=_platform_admin(B))
                self.assertEqual(response.status_code, 200)
                names = {row.get("name") or row.get("agent_name") for row in response.json()[key]}
                self.assertIn(B_ROOT, names)
                self.assertNotIn(A_ROOT, names)


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

    def test_a2a_reaches_the_agents_a_member_may_use(self):
        self._as_member(ALICE)
        self.assertEqual(self.client.post(f"/a2a/{B_ROOT}", json={}).status_code, 403)
        self.assertEqual(self.client.get(f"/a2a/{B_ROOT}/.well-known/agent-card.json").status_code, 403)
        self.assertEqual(self.client.post("/a2a/not_configured", json={}).status_code, 403)
        self.assertEqual(self.reached, [])
        self.assertEqual(self.client.post(f"/a2a/{A_ROOT}", json={}).status_code, 200)
        self.assertEqual(self.reached, [f"/a2a/{A_ROOT}"])
        # A2A runs the agent under an id of its own, so the member's roles count here
        session = self.Session()
        session.query(AgentConfig).filter_by(name=A_ROOT).update({"allowed_for_roles": '["finance"]'})
        session.commit()
        session.close()
        self.assertEqual(self.client.post(f"/a2a/{A_ROOT}", json={}).status_code, 403)

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


def _concrete(path: str) -> str:
    """The path with every parameter filled in, so it can be requested."""
    for name in ("template_id", "version", "job_id", "lead_id", "partner_key", "order_pk",
                 "user_id", "build_id"):
        path = path.replace("{" + name + "}", "1")
    return path


class TestWorkspaceAdmins(_TwoWorkspaces):
    """ctx.is_admin: bob owns workspace B and has no admin role, so he administers B and
    nothing else (phase 2e)."""

    def _as(self, user_id: str, workspace_id: int = None, **kwargs) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id, workspace_id), **kwargs)

    def _agent_form(self, project_id: int, name: str = "zz_ws_b_new", **fields) -> dict:
        return {"name": name, "type": "llm", "project_id": str(project_id),
                "description": "d", "instruction": "i", **fields}

    def test_an_owner_administers_their_workspace(self):
        bob = self._as(BOB)
        agents = bob.get("/dashboard/api/agents")
        self.assertEqual(agents.status_code, 200)
        self.assertIn(B_ROOT, agents.text)
        self.assertNotIn(A_ROOT, agents.text)
        response = bob.post("/dashboard/api/agents", data=self._agent_form(self.ids["project_b"]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["success"])

    def test_an_owner_is_no_admin_of_another_workspace(self):
        bob = self._as(BOB)
        self.assertEqual(bob.get("/dashboard/api/agents", headers={WORKSPACE_HEADER: str(A)})
                         .status_code, 404)

    def test_a_member_is_no_admin(self):
        response = self._as(ALICE).get("/dashboard/api/agents")
        self.assertEqual((response.status_code, response.json()),
                         (403, {"detail": "Admin access required"}))

    def test_an_owner_is_refused_every_platform_only_route(self):
        bob = self._as(BOB)
        reached = []
        with patch("shared.utils.audit_service.log"):
            for method, path in sorted(PLATFORM_ONLY):
                response = bob.request(method, _concrete(path))
                if response.status_code != 403 or response.json() != {
                        "detail": "Platform administrator access required"}:
                    reached.append(f"{method} {path} -> {response.status_code}")
        self.assertFalse(reached, "An owner of a workspace reached these platform-only routes:\n"
                         + "\n".join(reached))

    def test_the_middleware_refuses_exactly_the_platform_only_routes(self):
        disagree = [f"{method} {path}" for route in _api_routes() for method, path in _pairs(route)
                    if is_platform_only(method, _concrete(path)) != ((method, path) in PLATFORM_ONLY)]
        self.assertFalse(disagree, "server/dashboard_authz.py PLATFORM_ONLY_PATHS and PLATFORM_ONLY "
                         "disagree on:\n" + "\n".join(disagree))

    def test_the_installation_s_pages_are_for_platform_administrators(self):
        bob = self._as(BOB, follow_redirects=False)
        for page in ("/dashboard/users", "/dashboard/migrations", "/dashboard/settings",
                     "/dashboard/wizard-leads", "/dashboard/wizard-pricing", "/dashboard/shop-orders"):
            with self.subTest(page=page):
                self.assertIn(bob.get(page).status_code, (302, 303, 307))
        for page, platform_only in (("/dashboard/overview", 'href="/dashboard/users"'),
                                    ("/dashboard/overview", 'href="/dashboard/migrations"'),
                                    ("/dashboard/overview", 'id="adkStatusIndicator"'),
                                    ("/dashboard/agents", 'data-click="reinitializeAllAgents"')):
            with self.subTest(page=page, platform_only=platform_only):
                shown = bob.get(page)
                self.assertEqual(shown.status_code, 200)
                self.assertNotIn(platform_only, shown.text)
                self.assertIn(platform_only, self.client.get(page, headers=_platform_admin(A)).text)

    def test_an_owner_cannot_give_an_agent_what_runs_code_on_the_server(self):
        bob = self._as(BOB)
        project = self.ids["project_b"]
        stdio = {"mcpServers": {"x": {"command": "sh", "args": ["-c", "id"]}}}
        for fields in ({"tool_config": json.dumps({"code_executor": {}})},
                       {"mcp_servers_config": json.dumps(stdio)},
                       {"model_api_key": "${MATE_SECRET_KEY}", "model_base_url": "https://evil.example"},
                       {"mcp_servers_config": json.dumps({"mcpServers": {"h": {
                           "url": "https://evil.example", "headers": {"x": "${OPENAI_API_KEY}"}}}})},
                       {"hardcoded": "true"}):
            with self.subTest(fields=fields):
                response = bob.post("/dashboard/api/agents", data=self._agent_form(project, **fields))
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIn("platform administrator", response.json()["detail"])
        # A platform administrator can, in any workspace
        response = self.client.post("/dashboard/api/agents", headers=_platform_admin(B),
                                    data=self._agent_form(project, tool_config=json.dumps(
                                        {"code_executor": {}})))
        self.assertTrue(response.json()["success"], response.text)

    def test_an_owner_keeps_what_a_platform_administrator_set_up_as_it_is(self):
        session = self.Session()
        config = session.query(AgentConfig).filter_by(name=B_ROOT).one()
        config.tool_config = json.dumps({"code_executor": {"timeout": 5}})
        config.model_api_key = "${OPENAI_API_KEY}"
        config_id = config.id
        session.commit()
        session.close()
        bob = self._as(BOB)

        def update(**fields) -> httpx.Response:
            form = self._agent_form(self.ids["project_b"], name=B_ROOT, model_api_key="__stored__",
                                    tool_config=json.dumps({"code_executor": {"timeout": 5}}))
            form.update(fields)
            return bob.put(f"/dashboard/api/agents/{config_id}", data=form)

        with patch("shared.utils.dashboard.dashboard_server.STORED_SECRET_SENTINEL", "__stored__"):
            self.assertTrue(update(instruction="new").json()["success"])
            self.assertEqual(update(tool_config=json.dumps({"code_executor": {"timeout": 500}}))
                             .status_code, 403)
            # A base URL of his own beside the ${VAR} key would send the variable there
            self.assertEqual(update(model_base_url="https://evil.example").status_code, 403)

    def test_an_owner_cannot_take_a_name_with_code_in_agents(self):
        root = Path(tempfile.mkdtemp())
        (root / "agents" / "zz_ws_b_coded").mkdir(parents=True)
        dashboard = auth_server.dashboard_server
        with patch.object(dashboard, "project_root", root):
            response = self._as(BOB).post("/dashboard/api/agents", data=self._agent_form(
                self.ids["project_b"], name="zz_ws_b_coded"))
            self.assertEqual(response.status_code, 409)
            response = self.client.post("/dashboard/api/agents", headers=_platform_admin(B),
                                        data=self._agent_form(self.ids["project_b"], name="zz_ws_b_coded"))
            self.assertTrue(response.json()["success"], response.text)

    def test_an_import_by_an_owner_skips_what_runs_code(self):
        response = self._as(BOB).post("/dashboard/api/agents/import", json={"agents": [
            {"name": "zz_ws_b_imported", "type": "llm", "project_id": self.ids["project_b"],
             "tool_config": json.dumps({"code_executor": {}})}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported_count"], 0)
        self.assertIn("platform administrator", " ".join(response.json()["errors"]))

    def test_a_rollback_by_an_owner_brings_back_no_code(self):
        session = self.Session()
        config = session.query(AgentConfig).filter_by(name=B_ROOT).one()
        version = AgentConfigVersion(agent_config_id=config.id, version_number=2)
        version.set_snapshot({**config.to_dict(), "tool_config": json.dumps({"code_executor": {}})})
        session.add(version)
        session.commit()
        ids = (config.id, version.id)
        session.close()
        response = self._as(BOB).post("/dashboard/api/agents/%s/rollback/%s" % ids)
        self.assertEqual(response.status_code, 403, response.text)

    def test_templates_bring_an_owner_no_code(self):
        dashboard = auth_server.dashboard_server
        template = {"project": {"name": "zz-ws-b-from-template"}, "template_meta": {"agent_prefix": "tpl_"},
                    "agents": [{"name": "tpl_root", "type": "llm", "description": "d", "instruction": "i",
                                "tool_config": {"code_executor": {}}}]}
        session = self.Session()
        session.query(Project).filter_by(id=self.ids["project_b"]).update(
            {"template_id": "coded", "template_prefix": "tpl_"})
        session.commit()
        session.close()
        bob = self._as(BOB)
        with patch.object(dashboard.template_service, "get_template", return_value=template):
            response = bob.post("/dashboard/api/templates/import", json={"template_id": "coded"})
            self.assertEqual(response.status_code, 403, response.text)
            response = bob.post("/dashboard/api/templates/sync",
                                json={"project_id": self.ids["project_b"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["agents_added"], 0)
        self.assertEqual(len(response.json()["agents_skipped"]), 1)

    def test_a_clone_by_an_owner_brings_no_code_and_no_secret(self):
        bob = self._as(BOB)
        project = self.ids["project_b"]

        def clone(client: TestClient, suffix: str, **headers) -> httpx.Response:
            return client.post(f"/dashboard/api/agents/{B_ROOT}/clone", headers=headers,
                               json={"target_project_id": project, "suffix": suffix})

        # The rename writes the suffix into the config, so it holds a name's characters only
        session = self.Session()
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"mcp_servers_config": json.dumps(
            {"mcpServers": {"s": {"url": f"https://example.com/{B_ROOT}"}}})})
        session.commit()
        for suffix in ("${MATE_SECRET_KEY}", '"x', "a-b"):
            self.assertEqual(clone(bob, suffix).status_code, 400, suffix)
        # A stdio server naming the agent: the rename would change its arguments
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"mcp_servers_config": json.dumps(
            {"mcpServers": {"s": {"command": "npx", "args": ["server", B_ROOT]}}})})
        session.commit()
        session.close()
        response = clone(bob, "_copy")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.Session().query(AgentConfig).filter(
            AgentConfig.name.like(f"{B_ROOT}_copy%")).count(), 0)
        self.assertEqual(clone(self.client, "_copy", **_platform_admin(B)).status_code, 200)

    def test_a_clone_by_an_owner_takes_no_name_with_code_in_agents(self):
        root = Path(tempfile.mkdtemp())
        (root / "agents" / f"{B_ROOT}_x").mkdir(parents=True)
        dashboard = auth_server.dashboard_server
        with patch.object(dashboard, "project_root", root):
            response = self._as(BOB).post(f"/dashboard/api/agents/{B_ROOT}/clone", json={
                "target_project_id": self.ids["project_b"], "suffix": "_x"})
        self.assertEqual(response.status_code, 200, response.text)
        names = {c["new"] for c in response.json()["cloned"]}
        self.assertNotIn(f"{B_ROOT}_x", names)
        self.assertIn(f"{B_ROOT}_x_2", names)

    def test_a_template_by_an_owner_takes_no_name_with_code_in_agents(self):
        root = Path(tempfile.mkdtemp())
        (root / "agents" / "coded_root").mkdir(parents=True)
        dashboard = auth_server.dashboard_server
        template = {"project": {"name": "p"}, "template_meta": {"agent_prefix": "tpl_"},
                    "agents": [{"name": "tpl_root", "type": "llm", "description": "d",
                                "instruction": "i"}]}
        bob = self._as(BOB)
        with patch.object(dashboard, "project_root", root), \
                patch.object(dashboard.template_service, "get_template", return_value=template):
            response = bob.post("/dashboard/api/templates/import",
                                json={"template_id": "t", "project_name": "coded"})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.Session().query(Project).filter_by(name="coded").count(), 0)
            # A sync after renaming the project the same
            session = self.Session()
            session.query(Project).filter_by(id=self.ids["project_b"]).update(
                {"name": "coded", "template_id": "t", "template_prefix": "tpl_"})
            session.commit()
            session.close()
            response = bob.post("/dashboard/api/templates/sync", json={"project_id": self.ids["project_b"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["agents_added"], response.json()["agents_skipped"]),
                         (0, [{"name": "coded_root", "reason": "this name is taken"}]))

    def test_an_owner_cannot_delete_a_hardcoded_agent_s_folder(self):
        session = self.Session()
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"hardcoded": True})
        session.commit()
        session.close()
        response = self._as(BOB).delete(f"/dashboard/api/agents/{B_ROOT}/delete-folder")
        self.assertEqual(response.status_code, 403, response.text)

    def test_an_agent_name_is_one_folder_under_agents(self):
        bob = self._as(BOB)
        project = self.ids["project_b"]
        for name in ("..", ".", "/tmp/x", "a/b", "%", "a-b", "1a"):
            with self.subTest(name=name):
                response = bob.post("/dashboard/api/agents", data=self._agent_form(project, name=name))
                self.assertEqual(response.status_code, 400, response.text)
        config_id = self.Session().query(AgentConfig.id).filter_by(name=B_ROOT).scalar()
        rename = bob.put(f"/dashboard/api/agents/{config_id}", data=self._agent_form(project, name=".."))
        self.assertEqual(rename.status_code, 400, rename.text)
        response = bob.post("/dashboard/api/agents/import", json={"agents": [
            {"name": "..", "type": "llm", "project_id": project}]})
        self.assertEqual(response.json()["imported_count"], 0)
        self.assertIn("not a valid agent name", " ".join(response.json()["errors"]))
        # An agent keeps a name it already has
        session = self.Session()
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"name": "zz-ws-b-legacy"})
        session.commit()
        session.close()
        kept = bob.put(f"/dashboard/api/agents/{config_id}",
                       data=self._agent_form(project, name="zz-ws-b-legacy"))
        self.assertTrue(kept.json()["success"], kept.text)

    def test_deleting_an_agent_deletes_no_folder_but_its_own(self):
        root = Path(tempfile.mkdtemp())
        (root / "agents" / "zz_ws_b_sub").mkdir(parents=True)
        (root / "keep.txt").write_text("kept")
        session = self.Session()
        # Names from before names were checked: the installation, and another agent's folder
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"name": ".."})
        session.add(AgentConfig(name="zz_ws_b_sub/.", type="llm", project_id=self.ids["project_b"]))
        session.commit()
        ids = dict(session.query(AgentConfig.name, AgentConfig.id).filter(
            AgentConfig.name.in_(["..", "zz_ws_b_sub/.", "zz_ws_b_sub"])))
        session.close()
        bob = self._as(BOB)
        with patch.object(auth_server.dashboard_server, "project_root", root), \
                patch("shared.utils.audit_service.log"):
            for name in ("..", "zz_ws_b_sub/."):
                response = bob.delete(f"/dashboard/api/agents/{ids[name]}?delete_folder=true")
                self.assertTrue(response.json()["success"], response.text)
                self.assertFalse(response.json()["folder_deleted"], name)
                self.assertTrue((root / "keep.txt").exists())
                self.assertTrue((root / "agents" / "zz_ws_b_sub").is_dir(), name)
            response = bob.delete(f"/dashboard/api/agents/{ids['zz_ws_b_sub']}?delete_folder=true")
        self.assertTrue(response.json()["folder_deleted"], response.text)
        self.assertFalse((root / "agents" / "zz_ws_b_sub").exists())

    def test_moving_a_root_agent_moves_its_own_children_only(self):
        session = self.Session()
        other = Project(name=f"{B_MARKER}-other", workspace_id=B)
        session.add(other)
        session.flush()
        # A name from before names were checked: as a LIKE pattern it matches every child
        wildcard = AgentConfig(name="%", type="llm", model_name="m", project_id=self.ids["project_b"])
        # And an agent of A naming it, which only the workspace tells apart
        session.add_all([wildcard, AgentConfig(name="ws_a_stale", type="llm", parent_agents='["%"]',
                                               project_id=self.ids["project_a"])])
        session.commit()
        other_id, wildcard_id = other.id, wildcard.id
        b_root_id = session.query(AgentConfig.id).filter_by(name=B_ROOT).scalar()
        session.close()
        bob = self._as(BOB)

        def project_of(name: str) -> int:
            return self.Session().query(AgentConfig.project_id).filter_by(name=name).scalar()

        moved = bob.put(f"/dashboard/api/agents/{wildcard_id}",
                        data=self._agent_form(other_id, name="%", parent_agents="[]"))
        self.assertTrue(moved.json()["success"], moved.text)
        self.assertEqual(project_of("ws_a_sub"), self.ids["project_a"])
        self.assertEqual(project_of("ws_a_stale"), self.ids["project_a"])
        self.assertEqual(project_of("zz_ws_b_sub"), self.ids["project_b"])
        # Renamed as it moves, the root takes the children that name it as it was
        moved = bob.put(f"/dashboard/api/agents/{b_root_id}",
                        data=self._agent_form(other_id, name="zz_ws_b_renamed", parent_agents="[]"))
        self.assertTrue(moved.json()["success"], moved.text)
        self.assertEqual(project_of("zz_ws_b_sub"), other_id)
        self.assertEqual(project_of("ws_a_sub"), self.ids["project_a"])

    def test_the_agent_server_makes_no_folder_out_of_agents(self):
        from shared.utils.server_control_service import ServerControlService
        root = Path(tempfile.mkdtemp())
        (root / "shared" / "template_agent").mkdir(parents=True)
        (root / "shared" / "template_agent" / "agent.py").write_text("")
        session = self.Session()
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"name": "../outside"})
        session.commit()
        session.close()
        service = ServerControlService(adk_host="127.0.0.1", adk_port=8001,
                                       session_service_uri="sqlite://")
        service.project_root = root
        service._initialize_agent_folders()
        self.assertFalse((root / "outside").exists())
        self.assertTrue((root / "agents" / A_ROOT / "agent.py").exists())

    def test_a_placeholder_spelled_with_json_escapes_is_seen(self):
        headers = '{"x": "\\u0024{OPENAI_API_KEY}"}'
        servers = {"mcpServers": {"h": {"url": "https://evil.example", "headers": headers}}}
        # The runtime decodes the headers once more, so this is a placeholder there
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret"}):
            parsed = mcp_tools._parse_json_field(headers, "headers", "h", B_ROOT)
            self.assertEqual(mcp_tools._resolve_env({"headers": parsed}, "h", B_ROOT),
                             {"headers": {"x": "sk-secret"}})
        response = self._as(BOB).post("/dashboard/api/agents", data=self._agent_form(
            self.ids["project_b"], mcp_servers_config=json.dumps(servers)))
        self.assertEqual(response.status_code, 403, response.text)

    def test_a_new_base_url_takes_the_stored_key_again(self):
        session = self.Session()
        config = session.query(AgentConfig).filter_by(name=B_ROOT).one()
        config.model_api_key = "sk-stored"
        config.model_base_url = "https://provider.example/v1"
        config_id = config.id
        session.commit()
        session.close()
        bob = self._as(BOB)

        def update(**fields) -> httpx.Response:
            form = self._agent_form(self.ids["project_b"], name=B_ROOT, model_api_key="__stored__",
                                    model_base_url="https://provider.example/v1")
            form.update(fields)
            return bob.put(f"/dashboard/api/agents/{config_id}", data=form)

        self.assertTrue(update(instruction="new").json()["success"])
        refused = update(model_base_url="https://evil.example/v1")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("enter the key again", refused.json()["detail"])
        self.assertTrue(update(model_base_url="https://evil.example/v1",
                               model_api_key="sk-their-own").json()["success"])

    def test_outside_the_default_workspace_an_agent_runs_no_code(self):
        factory = ToolFactory()
        with patch.object(ToolFactory, "_agent_has_widget_key", return_value=False), \
                patch("shared.utils.tools.code_executor_tools.create_code_executor_tools_from_config",
                      return_value=["executor"]):
            self.assertEqual(factory._create_code_executor_tools({"name": A_ROOT}), ["executor"])
            self.assertEqual(factory._create_code_executor_tools({"name": B_ROOT}), [])
        stdio = {"mcpServers": {"x": {"command": "npx", "args": ["server"]}}}
        with patch.object(mcp_tools, "create_mcp_toolset_command", return_value="toolset") as started:
            self.assertEqual(mcp_tools.create_mcp_tools_from_config(
                {"name": A_ROOT, "mcp_servers_config": stdio}), ["toolset"])
            self.assertEqual(mcp_tools.create_mcp_tools_from_config(
                {"name": B_ROOT, "mcp_servers_config": stdio}), [])
        self.assertEqual(started.call_count, 1)
        # The LangGraph runtime the same
        from shared.utils.langgraph import mcp_client
        with patch.object(mcp_client, "resolve_mcp_command", return_value="/bin/npx"):
            server = stdio["mcpServers"]["x"]
            self.assertIsNotNone(mcp_client._stdio_connection("x", server, A_ROOT))
            self.assertIsNone(mcp_client._stdio_connection("x", server, B_ROOT))


class TestWorkspaceSwitcher(_TwoWorkspaces):

    def setUp(self):
        super().setUp()
        # bob is a member of A too
        session = self.Session()
        session.add(WorkspaceMember(workspace_id=A, user_id=BOB, role="member", agent_roles='["user"]'))
        session.commit()
        session.close()

    def _projects(self, client: TestClient) -> str:
        response = client.get("/dashboard/api/projects")
        self.assertEqual(response.status_code, 200, response.text)
        return response.text

    def test_the_switcher_lists_the_caller_s_workspaces(self):
        listed = TestClient(auth_server.app, cookies=_signed_in(ALICE)).get("/dashboard/api/workspaces")
        self.assertEqual([w["id"] for w in listed.json()["workspaces"]], [A])
        listed = TestClient(auth_server.app, cookies=_signed_in(BOB)).get("/dashboard/api/workspaces")
        self.assertEqual([(w["id"], w["role"]) for w in listed.json()["workspaces"]],
                         [(A, "member"), (B, "owner")])
        listed = self.client.get("/dashboard/api/workspaces", headers=_platform_admin(A))
        self.assertEqual([w["id"] for w in listed.json()["workspaces"]], [A, B])

    def test_the_session_works_in_the_workspace_it_switched_to(self):
        bob = TestClient(auth_server.app, cookies=_signed_in(BOB))
        # His first membership, where he is no admin
        self.assertEqual(bob.get("/dashboard/api/projects").status_code, 403)
        response = bob.put("/dashboard/api/workspaces/current", json={"workspace_id": B})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(B_MARKER, self._projects(bob))
        self.assertEqual(bob.get("/dashboard/api/workspaces").json()["current"], B)

    def test_a_workspace_the_caller_is_no_member_of_is_a_404(self):
        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE))
        for body in ({"workspace_id": B}, {"workspace_id": 99}):
            self.assertEqual(alice.put("/dashboard/api/workspaces/current", json=body).status_code, 404)
        self.assertEqual(alice.put("/dashboard/api/workspaces/current",
                                   json={"workspace_id": "2"}).status_code, 400)

    def test_a_session_s_workspace_the_caller_lost_is_left_for_the_default(self):
        session = self.Session()
        session.add(WorkspaceMember(workspace_id=B, user_id=ALICE, role="admin", agent_roles="[]"))
        session.commit()
        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE, B))
        self.assertIn(B_MARKER, self._projects(alice))
        session.query(WorkspaceMember).filter_by(workspace_id=B, user_id=ALICE).delete()
        session.commit()
        session.close()
        # Back in A, where she is no admin, rather than a 404 on every page
        self.assertEqual(alice.get("/dashboard/api/workspaces").json()["current"], A)

    def test_the_switcher_shows_with_more_than_one_workspace(self):
        def page(user_id: str) -> str:
            client = TestClient(auth_server.app, cookies=_signed_in(user_id))
            return client.get("/dashboard/workroom").text

        self.assertNotIn('id="workspaceSwitcher"', page(ALICE))
        switcher = page(BOB)
        self.assertIn('id="workspaceSwitcher"', switcher)
        self.assertIn(f"{B_MARKER} workspace", switcher)


if __name__ == "__main__":
    unittest.main()
