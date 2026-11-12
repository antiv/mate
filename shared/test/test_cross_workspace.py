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

import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, params
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
import httpx
import itsdangerous
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
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
    ChannelIntegration, EvalResult, FileSearchStore, GuardrailLog, LangGraphSession, MemoryBlock,
    PersonalAccessToken, Project, RateLimitConfig, ResponseFeedback, TestCase, TokenUsageLog, User,
    WidgetApiKey, Workspace, WorkspaceInvitation, WorkspaceMember,
)
from shared.utils.rbac_middleware import RBACMiddleware
from shared.utils.tools import mcp_tools
from shared.utils.tools.tool_factory import ToolFactory
from shared.utils.user_service import UserService
from shared.utils.alert_service import AlertService
from shared.utils.trigger_runner import TriggerRunner
from shared.utils.workspaces import (
    agent_workspace_for, agents_outside, caller_access, is_reserved_user_id, move_roles_to_members,
)

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
GET /dashboard/api/platform/workspaces
POST /dashboard/api/platform/workspaces
PUT /dashboard/api/platform/workspaces/{workspace_id}
POST /dashboard/api/platform/workspaces/{workspace_id}/suspend
POST /dashboard/api/platform/workspaces/{workspace_id}/resume
POST /dashboard/api/platform/workspaces/{workspace_id}/close
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
    ("POST", "/dashboard/api/members/leave"): "test_a_member_leaves_the_current_workspace",
    ("POST", "/dashboard/api/invitations"): "test_an_owner_invites_to_the_current_workspace",
    ("POST", "/dashboard/api/invitations/accept"): "test_an_invitation_is_accepted_once",
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
            "invitation_id": WorkspaceInvitation(
                workspace_id=B, email=f"invitee@{B_MARKER}.example", role="member", token_hash="0" * 64,
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
                created_by=BOB),
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
                "bob": BOB, "user_id": BOB, "lg_session_id": f"{B_MARKER}-lg-session"}


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
        # A trace is the workspace's its model calls record; one that records none is
        # the default workspace's, unless it names another workspace's agent
        for trace, agent, workspace in (("trace-a", A_ROOT, A), ("trace-b", B_ROOT, B),
                                        ("trace-none", None, None),
                                        ("trace-b-unrecorded", B_ROOT, None)):
            attributes = {"gen_ai.operation.name": "invoke_agent",
                          "gen_ai.conversation.id": f"session-{trace}"}
            if agent:
                attributes["gen_ai.agent.name"] = agent
            if workspace:
                attributes["mate.workspace.id"] = workspace
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
        self.assertEqual(response.status_code, 409)
        # Nor says in which workspace
        self.assertEqual(response.json()["detail"], "This Slack team is already connected. "
                         "If it is yours, ask a platform administrator.")

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

    def test_a_project_name_is_unique_within_its_workspace_only(self):
        with patch("shared.utils.audit_service.log"):
            # A's project name, in B (V048)
            response = self.client.post("/dashboard/api/projects", data={"name": "ws-a-project"},
                                        headers=_platform_admin(B))
            self.assertEqual(response.status_code, 200, response.text)
            again = self.client.post("/dashboard/api/projects", data={"name": "ws-a-project"},
                                     headers=_platform_admin(B))
        self.assertEqual((again.status_code, again.json()["detail"]),
                         (400, "Project name must be unique"))

    def test_a_template_whose_agent_names_another_workspace_holds_makes_no_project(self):
        # The project name is free in the default workspace, but the agent names it makes
        # are B's (D1). Outside the default workspace left_behind() refused it already.
        template = {"project": {"name": "zz ws b"},
                    "template_meta": {"agent_prefix": "tpl_", "root_agent": "tpl_root"},
                    "agents": [{"name": "tpl_root", "type": "llm"}]}
        dashboard = auth_server.dashboard_server
        with patch.object(dashboard.template_service, "get_template", return_value=template), \
                patch.object(dashboard, "_copy_template_agent"), patch("shared.utils.audit_service.log"):
            response = self.client.post("/dashboard/api/templates/import", headers=_platform_admin(A),
                                        json={"template_id": "t"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn(f"This name is taken: {B_ROOT}", response.text)
        session = self.Session()
        try:
            self.assertIsNone(session.query(Project).filter_by(name="zz ws b").first())
        finally:
            session.close()

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
        move_roles_to_members(session)
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

        self.a2a_users = []

        def agent_server(request: httpx.Request) -> httpx.Response:
            self.reached.append(request.url.path)
            self.a2a_users.append(request.headers.get("x-mate-a2a-user"))
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

    def test_run_live_keeps_to_the_member_s_workspaces(self):
        from starlette.websockets import WebSocketDisconnect
        connected = []

        def connect(url, *args, **kwargs):
            connected.append(url)
            raise ConnectionError("no agent server in this test")

        cookie = f"session={_signed_in(ALICE)['session']}"
        with patch.object(proxy_routes.websockets, "connect", side_effect=connect):
            with self.assertRaises(WebSocketDisconnect) as refused:
                with self.client.websocket_connect(
                        f"/run_live?app_name={B_ROOT}&user_id={ALICE}&session_id=s",
                        headers={"cookie": cookie}) as ws:
                    ws.receive_text()
            self.assertEqual(refused.exception.code, 1008)
            self.assertEqual(connected, [])
            try:
                with self.client.websocket_connect(
                        f"/run_live?app_name={A_ROOT}&user_id={ALICE}&session_id=s",
                        headers={"cookie": cookie}) as ws:
                    ws.receive_text()
            except WebSocketDisconnect:
                pass
        self.assertEqual(len(connected), 1)
        self.assertIn(f"app_name={A_ROOT}", connected[0])

    def test_a2a_runs_as_the_member_not_as_an_id_the_client_picks(self):
        self._as_member(ALICE)
        spoofed = {"x-mate-a2a-user": BOB}
        self.assertEqual(self.client.post(f"/a2a/{A_ROOT}", json={}, headers=spoofed).status_code, 200)
        self.assertEqual(self.a2a_users, [ALICE])

    def test_a2a_for_an_admin_keeps_a2a_s_own_id(self):
        response = self.client.post(f"/a2a/{A_ROOT}", json={},
                                    headers={**_platform_admin(A), "x-mate-a2a-user": BOB})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.a2a_users, [None])

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
        for page in ("/dashboard/users", "/dashboard/workspaces", "/dashboard/migrations",
                     "/dashboard/settings", "/dashboard/wizard-leads", "/dashboard/wizard-pricing", "/dashboard/shop-orders"):
            with self.subTest(page=page):
                self.assertIn(bob.get(page).status_code, (302, 303, 307))
        for page, platform_only in (("/dashboard/overview", 'href="/dashboard/users"'),
                                    ("/dashboard/overview", 'href="/dashboard/migrations"'),
                                    ("/dashboard/overview", 'href="/dashboard/workspaces"'),
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


class TestReviewOfPhase2(_TwoWorkspaces):
    """The paths the security review of phase 2 found (#277, 2f)."""

    def _as(self, user_id: str) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id))

    def test_a_subagent_runs_code_only_where_its_parent_may(self):
        # A subagent made by delegation has its parent's project and a name with no row
        factory = ToolFactory()
        subagent = "subagent_ab12cd"
        with patch.object(ToolFactory, "_agent_has_widget_key", return_value=False), \
                patch("shared.utils.tools.code_executor_tools.create_code_executor_tools_from_config",
                      return_value=["executor"]):
            self.assertEqual(factory._create_code_executor_tools(
                {"name": subagent, "project_id": self.ids["project_a"]}), ["executor"])
            self.assertEqual(factory._create_code_executor_tools(
                {"name": subagent, "project_id": self.ids["project_b"]}), [])
            self.assertEqual(factory._create_code_executor_tools(
                {"name": subagent, "project_id": 999}), [])
        stdio = {"mcpServers": {"x": {"command": "npx", "args": ["server"]}}}
        with patch.object(mcp_tools, "create_mcp_toolset_command", return_value="toolset"):
            self.assertEqual(mcp_tools.create_mcp_tools_from_config(
                {"name": subagent, "project_id": self.ids["project_b"], "mcp_servers_config": stdio}), [])

    def test_an_id_shaped_like_a_widget_visitor_s_is_no_visitor(self):
        # A new user joins the default workspace, A: the key of B's agent is what she'd use
        lookalike = f"widget_{self.b_rows['key_id']}_eve@example.com"
        session = self.Session()
        session.add(User(user_id=lookalike, roles='["user"]'))
        session.commit()
        session.close()
        rbac = RBACMiddleware.__new__(RBACMiddleware)
        rbac.user_service = UserService()
        allowed = rbac.check_agent_access(lookalike, {"name": B_ROOT, "allowed_for_roles": ["user"]})[0]
        self.assertFalse(allowed, "only the widget role the server gives out makes a visitor")
        for user_id in (lookalike, "widget_1_x", "trigger_runner"):
            self.assertTrue(is_reserved_user_id(user_id), user_id)
        self.assertFalse(is_reserved_user_id(ALICE))
        response = self.client.post("/dashboard/api/users", headers=_platform_admin(A),
                                    data={"user_id": "trigger_runner", "roles": '["admin"]'})
        self.assertFalse(response.json()["success"], response.text)

    def test_the_work_room_title_reads_one_session_of_the_caller(self):
        requested = []

        async def fake_get(client, url, **kwargs):
            requested.append(httpx.URL(url).raw_path)
            return httpx.Response(200, json={"state": {"_title": "t"}})

        escape = f"../../../../{B_ROOT}/users/{BOB}/sessions/s"
        with patch("httpx.AsyncClient.get", new=fake_get):
            response = self._as(ALICE).post("/dashboard/api/workroom/title", json={
                "agent": A_ROOT, "user_id": ALICE, "session_id": escape})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(requested[0].startswith(f"/apps/{A_ROOT}/users/".encode()), requested)

    def test_a_rating_is_of_a_session_of_the_caller_with_the_agent(self):
        session = self.Session()
        session.add(LangGraphSession(id="alice-session", app_name=A_ROOT, user_id=ALICE))
        session.commit()
        session.close()
        alice = self._as(ALICE)

        def rate(**fields) -> int:
            body = {"session_id": "alice-session", "message_id": "m1", "rating": "up",
                    "agent_name": A_ROOT, "user_id": ALICE, **fields}
            return alice.post("/dashboard/api/feedback", json=body).status_code

        from shared.utils.langgraph import session_store
        with patch.object(session_store, "_session_store", None):
            self.assertEqual(rate(), 200)
            # B's rating, rewritten with no agent or through one of A's
            self.assertEqual(rate(session_id="s", message_id="m", agent_name=None), 400)
            self.assertEqual(rate(session_id="s", message_id="m"), 404)
            self.assertEqual(rate(session_id=f"{B_MARKER}-lg-session"), 404)
            self.assertEqual(rate(user_id=BOB), 404)
        session = self.Session()
        self.assertEqual(session.query(ResponseFeedback).filter_by(session_id="s").one().rating, "down")
        session.close()

    def test_a_session_id_another_agent_also_has_is_not_mixed_up(self):
        path = Path(tempfile.mkdtemp()) / "sessions.db"
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT, state TEXT, "
                              "create_time REAL, update_time REAL)"))
            conn.execute(text("CREATE TABLE events (id TEXT, app_name TEXT, user_id TEXT, "
                              "session_id TEXT, event_data TEXT, timestamp REAL)"))
            for app, user in ((A_ROOT, ALICE), (B_ROOT, BOB)):
                conn.execute(text("INSERT INTO sessions VALUES (:a, :u, 'dup', '{}', 1, 1)"),
                             {"a": app, "u": user})
                conn.execute(text("INSERT INTO events VALUES (:i, :a, :u, 'dup', :d, 1)"),
                             {"i": f"e-{app}", "a": app, "u": user,
                              "d": json.dumps({"author": app})})
        engine.dispose()
        dashboard = auth_server.dashboard_server
        params = {"session_id": "dup", "app_name": A_ROOT, "user_id": ALICE, "runtime": "adk"}
        with patch.object(dashboard, "session_service_uri", f"sqlite:///{path}"):
            detail = self.client.get("/dashboard/api/sessions/detail", params=params,
                                     headers=_platform_admin(A)).json()
            self.assertEqual(detail["events"], [{"author": A_ROOT}])
            self.client.delete("/dashboard/api/sessions", params=params, headers=_platform_admin(A))
            self.assertIsNone(dashboard._get_session_detail("adk", A_ROOT, ALICE, "dup"))
            self.assertEqual(dashboard._get_session_detail("adk", B_ROOT, BOB, "dup")["events"],
                             [{"author": B_ROOT}])

    def test_a_workspace_header_of_other_digits_is_a_404(self):
        # The byte a client sends, which Starlette reads as latin-1: '²'
        request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                           "headers": [(WORKSPACE_HEADER.lower().encode(), b"\xb2")]})
        with self.assertRaises(HTTPException) as raised:
            resolve_workspace(request, auth.AUTH_USERNAME, True)
        self.assertEqual(raised.exception.status_code, 404)

    def test_an_owner_edits_mcp_servers_beside_a_var_set_up_for_them(self):
        session = self.Session()
        config = session.query(AgentConfig).filter_by(name=B_ROOT).one()
        config.model_api_key = "${OPENAI_API_KEY}"
        config_id = config.id
        session.commit()
        session.close()
        form = {"name": B_ROOT, "type": "llm", "project_id": str(self.ids["project_b"]),
                "description": "d", "instruction": "i", "model_api_key": "__stored__",
                "mcp_servers_config": json.dumps({"mcpServers": {"h": {"url": "https://tools.example"}}})}
        with patch("shared.utils.dashboard.dashboard_server.STORED_SECRET_SENTINEL", "__stored__"):
            response = self._as(BOB).put(f"/dashboard/api/agents/{config_id}", data=form)
        self.assertTrue(response.json()["success"], response.text)

    def test_the_workspace_is_resolved_once_a_request(self):
        # An owner: the middleware resolves the workspace to see that he is its admin
        bob = self._as(BOB)
        with patch("server.request_context.resolve_workspace", wraps=resolve_workspace) as resolved:
            self.assertEqual(bob.get("/dashboard/api/agents").status_code, 200)
        self.assertEqual(resolved.call_count, 1)


class TestSecondReviewOfPhase2(_TwoWorkspaces):
    """The paths the second review of phase 2 found (#277, 2g)."""

    def _as(self, user_id: str) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id))

    def test_file_search_keeps_to_the_agent_s_workspace(self):
        from shared.utils.tools.file_search_tools import create_file_search_tools_from_config
        session = self.Session()
        session.add(FileSearchStore(store_name="ws-a-store", display_name="a",
                                    project_id=self.ids["project_a"]))
        session.commit()
        session.close()

        def stores(project_id: int, names: List[str]) -> List[str]:
            config = {"name": "x", "project_id": project_id, "tool_config": json.dumps(
                {"file_search": {"enabled": True, "store_names": names}})}
            with patch("shared.utils.tools.file_search_tools.query_file_search",
                       side_effect=lambda question, store_names, model: store_names):
                tools = create_file_search_tools_from_config(config)
                return tools[0]("q") if tools else []

        b_store = self.b_rows["store_name"]
        # tool_config is the agent's own: an admin of A names B's store there
        self.assertEqual(stores(self.ids["project_a"], [b_store, "ws-a-store"]), ["ws-a-store"])
        self.assertEqual(stores(self.ids["project_b"], [b_store, "ws-a-store"]), [b_store])
        # A store made in Gemini directly, as 1.x allowed: in the default workspace only
        self.assertEqual(stores(self.ids["project_a"], ["fileSearchStores/made-elsewhere"]),
                         ["fileSearchStores/made-elsewhere"])
        self.assertEqual(stores(self.ids["project_b"], ["fileSearchStores/made-elsewhere"]), [])

    def _version_of_b_root(self, **snapshot) -> int:
        session = self.Session()
        version = AgentConfigVersion(agent_config_id=self.b_rows["config_id"], version_number=2,
                                     config_snapshot=json.dumps({"name": B_ROOT, **snapshot}))
        session.add(version)
        session.commit()
        version_id = version.id
        session.close()
        return version_id

    def _roll_back(self, version_id: int) -> int:
        return self._as(BOB).post(
            f"/dashboard/api/agents/{self.b_rows['config_id']}/rollback/{version_id}").status_code

    def test_a_rollback_checks_what_it_brings_back(self):
        # A version from before a platform administrator moved the agent to B, or
        # before its parent or its name were taken elsewhere
        cases = (
            ({"project_id": self.ids["project_a"]}, 404),
            ({"parent_agents": json.dumps([A_ROOT])}, 400),
            ({"name": A_ROOT}, 409),
            ({"name": "has_code"}, 409),
        )
        for snapshot, refused in cases:
            with self.subTest(snapshot=snapshot):
                with patch("shared.utils.dashboard.dashboard_server.has_code_folder",
                           side_effect=lambda root, name: name == "has_code"):
                    self.assertEqual(self._roll_back(self._version_of_b_root(**snapshot)), refused)
        session = self.Session()
        b_root = session.query(AgentConfig).filter_by(id=self.b_rows["config_id"]).one()
        self.assertEqual((b_root.name, b_root.project_id), (B_ROOT, self.ids["project_b"]))
        session.close()
        version_id = self._version_of_b_root(project_id=self.ids["project_b"], description="older")
        self.assertEqual(self._roll_back(version_id), 200)

    def test_a_clone_by_an_owner_copies_no_code_the_source_has(self):
        # Set on B's agent by a platform administrator; the rename leaves it as it is
        session = self.Session()
        session.query(AgentConfig).filter_by(name=B_ROOT).update({"mcp_servers_config": json.dumps(
            {"mcpServers": {"s": {"command": "npx", "args": ["server"]}}})})
        session.commit()
        session.close()
        body = {"target_project_id": self.ids["project_b"], "suffix": "_copy"}
        response = self._as(BOB).post(f"/dashboard/api/agents/{B_ROOT}/clone", json=body)
        self.assertEqual(response.status_code, 403, response.text)
        response = self.client.post(f"/dashboard/api/agents/{B_ROOT}/clone", json=body,
                                    headers=_platform_admin(B))
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_template_sync_checks_the_config_it_writes(self):
        dashboard = auth_server.dashboard_server
        slug = dashboard.template_service.slugify_project_name(f"{B_MARKER}-project")
        synced_name = f"{slug}_root"

        def stdio(arg: str) -> str:
            return json.dumps({"mcpServers": {"s": {"command": "npx", "args": ["server", arg]}}})

        template = {"template_meta": {"agent_prefix": "tpl_"},
                    "agents": [{"name": "tpl_root", "type": "llm", "description": "d",
                                "instruction": "i", "mcp_servers_config": stdio("tpl_root")}]}
        session = self.Session()
        session.query(Project).filter_by(id=self.ids["project_b"]).update(
            {"template_id": "t", "template_prefix": "tpl_"})
        # Set by a platform administrator
        session.add(AgentConfig(name=synced_name, type="llm", model_name="m",
                                project_id=self.ids["project_b"], mcp_servers_config=stdio("tpl_root")))
        session.commit()
        session.close()

        def sync() -> dict:
            with patch.object(dashboard.template_service, "get_template", return_value=template):
                response = self._as(BOB).post("/dashboard/api/templates/sync",
                                              json={"project_id": self.ids["project_b"]})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        # The sync maps the name in the arguments: a changed stdio server
        self.assertEqual([s["name"] for s in sync()["agents_skipped"]], [synced_name])
        session = self.Session()
        self.assertEqual(session.query(AgentConfig.mcp_servers_config).filter_by(
            name=synced_name).scalar(), stdio("tpl_root"))
        # The same server as the sync writes it is no change
        session.query(AgentConfig).filter_by(name=synced_name).update(
            {"mcp_servers_config": stdio(synced_name)})
        session.commit()
        session.close()
        self.assertEqual(sync()["agents_skipped"], [])

    def test_a_scored_output_is_stored_only_against_a_version_of_the_workspace(self):
        session = self.Session()
        a_root = session.query(AgentConfig).filter_by(name=A_ROOT).one()
        a_version = AgentConfigVersion(agent_config_id=a_root.id, version_number=1,
                                       config_snapshot=json.dumps({"name": A_ROOT}))
        session.add(a_version)
        session.commit()
        a_version_id = a_version.id
        session.close()
        bob = self._as(BOB)
        path = f"/dashboard/api/evals/{self.b_rows['test_case_id']}/run"
        response = bob.post(path, json={"version_id": a_version_id, "actual_output": B_MARKER})
        self.assertEqual(response.status_code, 404, response.text)
        session = self.Session()
        self.assertEqual(session.query(EvalResult).filter_by(version_id=a_version_id).count(), 0)
        session.close()
        response = bob.post(path, json={"version_id": self.b_rows["version_id"], "actual_output": B_MARKER})
        self.assertEqual(response.status_code, 200, response.text)


class TestThirdReviewOfPhase2(_TwoWorkspaces):
    """The paths the third review of phase 2 found (#277, 2h)."""

    def _as(self, user_id: str) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id))

    def test_a_multi_parent_instance_runs_code_only_where_its_agent_may(self):
        from shared.utils.agent_manager import AgentManager
        session = self.Session()
        session.query(AgentConfig).filter_by(name="zz_ws_b_sub").update({
            "parent_agents": json.dumps([B_ROOT, "zz_ws_b_other"]),
            "tool_config": json.dumps({"code_executor": {}})})
        session.commit()
        sub = session.query(AgentConfig).filter_by(name="zz_ws_b_sub").one()
        session.expunge(sub)
        session.close()
        manager = AgentManager.__new__(AgentManager)
        manager.initialized_agents = {"zz_ws_b_sub": object()}
        manager.db_client = MagicMock()
        built = {}

        def initialize(config: dict, children: list) -> object:
            # What the tool factory gives the instance
            built[config["name"]] = ToolFactory()._create_code_executor_tools(config)
            return object()

        with patch.object(manager, "_initialize_agent", side_effect=initialize), \
                patch("shared.utils.agent_config_identity.record_for_row"), \
                patch.object(ToolFactory, "_agent_has_widget_key", return_value=False), \
                patch("shared.utils.tools.code_executor_tools.create_code_executor_tools_from_config",
                      return_value=["executor"]):
            manager._get_or_create_subagent_instance(sub, "zz_ws_b_other")
        self.assertEqual(built, {"zz_ws_b_sub_zz_ws_b_other": []})

    def test_spend_with_sub_agents_keeps_to_the_agent_s_workspace(self):
        from shared.utils.token_usage_service import _agent_tree
        # A sub-agent of A that names a parent by a name B uses (allowed in 1.x)
        session = self.Session()
        session.query(AgentConfig).filter_by(name="ws_a_sub").update(
            {"parent_agents": json.dumps([A_ROOT, B_ROOT])})
        session.commit()
        self.assertEqual(_agent_tree(session, B_ROOT), [B_ROOT, "zz_ws_b_sub"])
        self.assertEqual(_agent_tree(session, A_ROOT), [A_ROOT, "ws_a_sub"])
        session.close()

    def test_a_slack_user_from_before_1_6_joins_the_agent_s_workspace(self):
        from server.slack_routes import _carry_over_roles
        session = self.Session()
        session.add(User(user_id="slack_U1", roles='["user", "finance"]'))
        session.commit()
        session.close()
        # The old id's roles carry over in the default workspace only (2j)
        _carry_over_roles("slack_T_A_U1", "slack_U1", A_ROOT)
        session = self.Session()
        self.assertEqual(session.query(WorkspaceMember.workspace_id, WorkspaceMember.agent_roles)
                         .filter_by(user_id="slack_T_A_U1").all(), [(A, '["user", "finance"]')])
        session.close()

    def test_a_token_of_a_closed_workspace_stops_working(self):
        bearer = {"Authorization": f"Bearer {PATS[BOB]}"}
        self.assertEqual(self.client.get("/v1/models", headers=bearer).status_code, 200)
        session = self.Session()
        session.query(Workspace).filter_by(id=B).update(
            {"deleted_at": datetime.now(timezone.utc).replace(tzinfo=None)})
        session.commit()
        session.close()
        self.assertEqual(self.client.get("/v1/models", headers=bearer).status_code, 403)

    def test_a_test_case_links_only_a_version_of_the_workspace(self):
        session = self.Session()
        a_root = session.query(AgentConfig).filter_by(name=A_ROOT).one()
        a_version = AgentConfigVersion(agent_config_id=a_root.id, version_number=1,
                                       config_snapshot=json.dumps({"name": A_ROOT}))
        session.add(a_version)
        session.commit()
        a_version_id = a_version.id
        session.close()
        bob = self._as(BOB)
        case = {"agent_name": B_ROOT, "input": "q", "expected_output": "a"}
        self.assertEqual(bob.post("/dashboard/api/evals", json={**case, "version_id": a_version_id})
                         .status_code, 404)
        path = f"/dashboard/api/evals/{self.b_rows['test_case_id']}"
        self.assertEqual(bob.put(path, json={"version_id": a_version_id}).status_code, 404)
        self.assertEqual(bob.put(path, json={"version_id": self.b_rows["version_id"]}).status_code, 200)

    def test_tools_on_the_server_s_credentials_are_platform_only(self):
        from shared.utils import platform_only
        for tools in ({"google_drive": True}, {"cv_tools": True},
                      {"google_calendar": {"calendar_id": "c@x"}}, {"supabase_storage": True}):
            with self.subTest(tools=tools):
                # On save: refused to a workspace admin
                response = self._as(BOB).post("/dashboard/api/agents", data=self._agent_form(
                    self.ids["project_b"], tool_config=json.dumps(tools)))
                self.assertEqual(response.status_code, 403, response.text)
                # At run time: left out outside the default workspace
                tool_type = next(iter(tools))
                for project, expected in ((self.ids["project_a"], 1), (self.ids["project_b"], 0)):
                    factory = ToolFactory()
                    factory._tool_creators[tool_type] = lambda config: ["tool"]
                    built = factory.create_tools({"name": "x", "project_id": project,
                                                  "tool_config": json.dumps(tools)})
                    self.assertEqual(built.count("tool"), expected, project)
        # Another calendar is a change, as a new command for a stdio server is
        before = {"tool_config": json.dumps({"google_calendar": {"calendar_id": "a@x"}})}
        after = {"tool_config": json.dumps({"google_calendar": {"calendar_id": "b@x"}})}
        self.assertEqual(platform_only.features_added(after, before), {platform_only.SERVER_CREDENTIALS})
        self.assertEqual(platform_only.features_added(before, before), set())

    def _agent_form(self, project_id: int, name: str = "zz_ws_b_new", **fields) -> dict:
        return TestWorkspaceAdmins._agent_form(self, project_id, name, **fields)

    def test_outbound_requests_of_a_workspace_stay_off_the_server_s_network(self):
        from shared.utils import outbound
        from shared.utils.langgraph import mcp_client
        from shared.utils.trigger_runner import TriggerRunner
        from shared.utils.utils import resolve_agent_endpoint
        internal, public = "http://127.0.0.1:8001/api/reload-all-agents", "http://93.184.216.34/hook"
        self.assertIsNotNone(outbound.outbound_refusal(internal, workspace_id=B))
        self.assertIsNotNone(outbound.outbound_refusal("http://169.254.169.254/", workspace_id=B))
        self.assertIsNone(outbound.outbound_refusal(public, workspace_id=B))
        # The installation's own configuration may point inside (a local Ollama)
        self.assertIsNone(outbound.outbound_refusal(internal, workspace_id=A))
        self.assertIsNone(outbound.outbound_refusal(internal, agent_name="no_config_agent"))
        self.assertIsNotNone(outbound.outbound_refusal(internal, project_id=self.ids["project_b"]))
        with patch.dict(os.environ, {"OUTBOUND_ALLOW_PRIVATE_NETWORK": "true"}):
            self.assertIsNone(outbound.outbound_refusal(internal, workspace_id=B))

        # An alert rule's webhook, its test button too
        session = self.Session()
        session.query(AlertRule).filter_by(id=self.b_rows["rule_id"]).update(
            {"destination_config": json.dumps({"url": internal})})
        session.commit()
        session.close()
        with patch("shared.utils.alert_service.post_json") as sent:
            response = self._as(BOB).post(f"/dashboard/api/alert-rules/{self.b_rows['rule_id']}/test")
        self.assertIn("internal address", response.text)
        sent.assert_not_called()

        # A trigger's callback
        with patch("shared.utils.trigger_runner.post_json") as sent:
            with self.assertRaises(RuntimeError):
                TriggerRunner._output_http_callback(MagicMock(), {"url": internal}, "x",
                                                    self.ids["project_b"], B_ROOT)
        sent.assert_not_called()

        # An agent's MCP server, on both runtimes, and its model endpoint
        servers = {"mcpServers": {"s": {"url": internal}}}
        with patch.object(mcp_tools, "create_mcp_toolset_http", return_value="toolset") as made:
            for project, expected in ((self.ids["project_a"], ["toolset"]), (self.ids["project_b"], [])):
                self.assertEqual(mcp_tools.create_mcp_tools_from_config(
                    {"name": "x", "project_id": project, "mcp_servers_config": servers}), expected)
        self.assertEqual(made.call_count, 1)
        self.assertIsNone(mcp_client._http_connection("s", {"url": internal}, "x", self.ids["project_b"]))
        self.assertIsNotNone(mcp_client._http_connection("s", {"url": internal}, "x", self.ids["project_a"]))
        endpoint = {"name": "x", "model_name": "openai/m", "model_base_url": "http://127.0.0.1:11434/v1"}
        with self.assertRaises(ValueError):
            resolve_agent_endpoint({**endpoint, "project_id": self.ids["project_b"]})
        self.assertEqual(resolve_agent_endpoint({**endpoint, "project_id": self.ids["project_a"]})[0],
                         "http://127.0.0.1:11434/v1")


class TestFourthReviewOfPhase2(_TwoWorkspaces):
    """The paths the fourth review of phase 2 found (#277, 2i)."""

    def _as(self, user_id: str) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id))

    def test_drive_s_mcp_server_is_the_default_workspace_s(self):
        from fastapi import FastAPI
        from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer
        app = FastAPI()
        GoogleDriveMCPServer(app, gdrive_mcp_available=True)
        client = TestClient(app)
        path = "/gdrive/mcp/tools/list"
        for headers, expected in (({"Authorization": f"Bearer {PATS[ALICE]}"}, 200),
                                  ({"Authorization": f"Bearer {PATS[BOB]}"}, 403),
                                  (_platform_admin(B), 200)):
            self.assertEqual(client.post(path, headers=headers).status_code, expected, headers)

    def test_a_name_taken_after_a_delete_inherits_no_ratings(self):
        # Left by A's agent of that name, deleted before B took the name
        session = self.Session()
        left = ResponseFeedback(session_id="s-a", message_id="m-a", agent_name=B_ROOT,
                                project_id=self.ids["project_a"], rating="down",
                                comment="A_ONLY", question="A_ONLY", answer="A_ONLY")
        session.add(left)
        session.commit()
        left_id = left.id
        session.close()
        bob = self._as(BOB)
        listed = bob.get("/dashboard/api/evals/feedback")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("A_ONLY", listed.text)
        self.assertIn(B_MARKER, listed.text)
        self.assertEqual(bob.post("/dashboard/api/evals/improve/propose",
                                  json={"feedback_id": left_id}).status_code, 404)
        self.assertEqual(bob.post("/dashboard/api/evals", json={
            "agent_name": B_ROOT, "input": "q", "expected_output": "a",
            "source_feedback_id": left_id}).status_code, 400)

    def test_a_trace_recorded_in_another_workspace_is_not_shown(self):
        session = self.Session()
        session.execute(text(
            "CREATE TABLE trace_spans (id INTEGER PRIMARY KEY, trace_id TEXT, span_id TEXT, "
            "parent_span_id TEXT, name TEXT, kind TEXT, start_time DATETIME, end_time DATETIME, "
            "duration_ms INTEGER, attributes TEXT, status TEXT, error_message TEXT)"))
        now = datetime.now()
        for trace, workspace_id in (("trace-of-a", A), ("trace-of-b", B)):
            session.execute(text(
                "INSERT INTO trace_spans (trace_id, span_id, name, start_time, end_time, attributes) "
                "VALUES (:t, 's', 'invoke_agent', :st, :st, :a)"),
                {"t": trace, "st": now, "a": json.dumps(
                    {"gen_ai.agent.name": B_ROOT, "mate.workspace.id": workspace_id})})
        session.commit()
        session.close()
        response = self.client.get("/dashboard/api/traces", headers=_platform_admin(B))
        self.assertEqual([t["trace_id"] for t in response.json()["traces"]], ["trace-of-b"])

    def test_the_server_s_mail_and_shop_orders_are_the_default_workspace_s(self):
        from shared.utils import platform_only, shop_service
        from shared.utils.alert_service import AlertService
        from shared.utils.tools.shop_tools import create_shop_tools_from_config
        from shared.utils.trigger_runner import TriggerRunner
        mailed = {"shop": {"vendor_email": "victim@example.com"}}
        self.assertEqual(platform_only.features_added({"tool_config": json.dumps(mailed)}),
                         {platform_only.SERVER_CREDENTIALS})
        self.assertEqual(platform_only.features_added(
            {"tool_config": json.dumps({"shop": {"shop_name": "S", "currency": "EUR"}})}), set())
        response = self._as(BOB).post("/dashboard/api/agents", data=self._agent_form(
            self.ids["project_b"], tool_config=json.dumps(mailed)))
        self.assertEqual(response.status_code, 403, response.text)

        # At run time: the cart works, the order is neither mailed nor stored
        config = {"shop": {"vendor_email": "victim@example.com", "partner_key": "partner"}}
        for project, expected in ((self.ids["project_a"], 1), (self.ids["project_b"], 0)):
            tools = {f.__name__: f for f in create_shop_tools_from_config(
                {"name": "x", "project_id": project, "tool_config": config})}
            context = MagicMock()
            context.state = {}
            product = tools["search_products"](tool_context=context)["products"][0]["id"]
            tools["add_to_cart"](product, tool_context=context)
            with patch.object(shop_service, "persist_order") as stored, \
                    patch.object(shop_service, "send_order_emails", return_value={}) as sent:
                self.assertIn("order_id", tools["place_order"]("C", "c@example.com", tool_context=context))
            self.assertEqual((stored.call_count, sent.call_count), (expected, expected), project)

        # A trigger's and an alert rule's email
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}), \
                patch("shared.utils.trigger_runner.send_email", return_value=(True, "ok")) as sent:
            with self.assertRaises(RuntimeError):
                TriggerRunner._output_email(MagicMock(), {"to": "x@example.com"}, "t",
                                            self.ids["project_b"], B_ROOT)
            sent.assert_not_called()
            TriggerRunner._output_email(MagicMock(), {"to": "x@example.com"}, "t",
                                        self.ids["project_a"], A_ROOT)
            sent.assert_called_once()
        rule = MagicMock(workspace_id=B, destination_type="email")
        rule.get_destination_config.return_value = {"to": "x@example.com"}
        with patch("shared.utils.alert_service.send_email", return_value=(True, "ok")) as sent:
            ok, detail = AlertService._deliver(MagicMock(), rule, {"message": "m"})
            self.assertFalse(ok)
            sent.assert_not_called()
            rule.workspace_id = A
            self.assertTrue(AlertService._deliver(MagicMock(), rule, {"message": "m"})[0])

    def _agent_form(self, project_id: int, name: str = "zz_ws_b_new", **fields) -> dict:
        return TestWorkspaceAdmins._agent_form(self, project_id, name, **fields)

    def test_a_model_endpoint_redirect_does_not_reach_the_server_s_network(self):
        import asyncio
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from shared.utils import outbound
        from shared.utils.langgraph.model_factory import create_chat_model
        from shared.utils.utils import ModelContext, create_model
        reached = []

        class Inside(BaseHTTPRequestHandler):
            def do_POST(self):
                reached.append(self.path)
                body = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                   "choices": [{"index": 0, "finish_reason": "stop", "message": {
                                       "role": "assistant", "content": "inside"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        inside = HTTPServer(("127.0.0.1", 0), Inside)

        class Outside(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(307)
                self.send_header("Location", f"http://localhost:{inside.server_port}/v1/chat/completions")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        outside = HTTPServer(("127.0.0.1", 0), Outside)
        for server in (inside, outside):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(inside.shutdown)
        self.addCleanup(outside.shutdown)
        base_url = f"http://127.0.0.1:{outside.server_port}/v1"
        address = outbound.internal_address

        def public_127(url: str):
            # 127.0.0.1 stands for a public host; localhost stays the server's own
            return None if "127.0.0.1" in url else address(url)

        async def ask(project_id: int) -> None:
            context = ModelContext(agent_name="x", project_id=project_id)
            model = create_model("openai/m", api_key="k", base_url=base_url, context=context)
            try:
                await model.llm_client.acompletion(
                    model="openai/m", messages=[{"role": "user", "content": "q"}], tools=None,
                    api_key="k", base_url=base_url, num_retries=0)
            except Exception:
                pass
            chat = create_chat_model("openai/m", api_key="k", base_url=base_url, context=context)
            try:
                await chat.ainvoke("q")
            except Exception:
                pass

        with patch.object(outbound, "internal_address", side_effect=public_127):
            asyncio.run(ask(self.ids["project_b"]))
            self.assertEqual(reached, [])
            # The default workspace follows it, as in 1.x
            asyncio.run(ask(self.ids["project_a"]))
            self.assertEqual(len(reached), 2)

    def test_conflicting_app_names_are_refused(self):
        body = json.dumps({"app_name": A_ROOT, "appName": B_ROOT, "user_id": BOB}).encode()
        self.assertEqual(proxy_routes._workspace_refusal("run_sse", BOB, body), "conflicting app names")


class TestFifthReviewOfPhase2(_TwoWorkspaces):
    """The paths the fifth review of phase 2 found (#277, 2j)."""

    def _as(self, user_id: str) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id))

    def test_slack_roles_from_before_1_6_carry_over_in_the_default_workspace_only(self):
        from server.slack_routes import _carry_over_roles
        session = self.Session()
        session.add(User(user_id="slack_U9", roles='["admin"]'))
        session.commit()
        session.close()
        # B's admin signs the events of B's integration, so the Slack user is theirs to name
        _carry_over_roles("slack_T_B_U9", "slack_U9", B_ROOT)
        _carry_over_roles("slack_T_A_U9", "slack_U9", A_ROOT)
        session = self.Session()
        self.assertIsNone(session.query(User).filter_by(user_id="slack_T_B_U9").first())
        self.assertEqual(session.query(User).filter_by(user_id="slack_T_A_U9").one().get_roles(), ["admin"])
        session.close()

    def test_a_lazy_stream_is_read_guarded(self):
        import asyncio
        from shared.utils import outbound
        seen = []

        class Lazy:
            # As LiteLLM's gemini stream: the request goes out on the first read
            def __aiter__(self):
                return self

            async def __anext__(self):
                if seen:
                    raise StopAsyncIteration
                seen.append(outbound._GUARDED.get())
                return "chunk"

            def __iter__(self):
                return self

            def __next__(self):
                if len(seen) > 1:
                    raise StopIteration
                seen.append(outbound._GUARDED.get())
                return "chunk"

        class Client:
            async def acompletion(self, **kwargs):
                return Lazy()

            def completion(self, **kwargs):
                return Lazy()

        guarded = outbound.GuardedCompletions(Client(), B)

        async def read() -> list:
            return [chunk async for chunk in await guarded.acompletion()]

        self.assertEqual(asyncio.run(read()), ["chunk"])
        self.assertEqual(list(guarded.completion()), ["chunk"])
        self.assertEqual(seen, [B, B])
        self.assertIsNone(outbound._GUARDED.get())

    def test_the_agent_tools_give_no_more_than_the_dashboard(self):
        from shared.utils.tools import create_agent_tool
        context = MagicMock()
        servers = json.dumps({"mcpServers": {"s": {"url": "https://mcp.example.com",
                                                   "headers": {"Authorization": "${SECRET}"}}}})
        with patch.object(create_agent_tool, "_is_admin_user", return_value=True), \
                patch.object(create_agent_tool, "reload_agent_cache", return_value={"success": True}):
            # Run by a platform administrator, an agent of B follows B's admin's instruction
            context.agent_name = B_ROOT
            refused = create_agent_tool.update_agent("zz_ws_b_sub", mcp_servers_config=servers,
                                                     tool_context=context)
            self.assertEqual(refused["status"], "error")
            self.assertIn("platform administrator", refused["error_message"])
            # A hardcoded agent's folder, in an agents/ of the test's own
            agents_dir = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: __import__("shutil").rmtree(agents_dir, ignore_errors=True))
            (agents_dir / "zz_coded").mkdir()
            with patch.object(create_agent_tool, "_AGENTS_DIR", agents_dir):
                for name in ("zz_coded", "../x"):
                    made = create_agent_tool.create_agent(name, "d", "i", tool_context=context)
                    self.assertEqual(made["status"], "error", name)
            # An agent of the default workspace, as in 1.x
            context.agent_name = A_ROOT
            self.assertEqual(create_agent_tool.update_agent(
                "ws_a_sub", mcp_servers_config=servers, tool_context=context)["status"], "success")
        session = self.Session()
        self.assertIsNone(session.query(AgentConfig).filter_by(name="zz_ws_b_sub").one().mcp_servers_config)
        session.close()

    def test_a_renamed_agent_takes_its_rows_along(self):
        session = self.Session()
        session.execute(text(
            "CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT, state TEXT, "
            "create_time DATETIME, update_time DATETIME, PRIMARY KEY (app_name, user_id, id))"))
        session.execute(text(
            "CREATE TABLE events (id TEXT, app_name TEXT, user_id TEXT, session_id TEXT, content TEXT, "
            "PRIMARY KEY (id, app_name, user_id, session_id), FOREIGN KEY (app_name, user_id, session_id) "
            "REFERENCES sessions (app_name, user_id, id) ON DELETE CASCADE)"))
        session.execute(text("INSERT INTO sessions VALUES (:a, :u, 's1', '{}', NULL, NULL)"),
                        {"a": B_ROOT, "u": BOB})
        session.execute(text("INSERT INTO events VALUES ('e1', :a, :u, 's1', :c)"),
                        {"a": B_ROOT, "u": BOB, "c": B_MARKER})
        session.commit()
        config_id = self.b_rows["config_id"]
        session.close()
        renamed = self._as(BOB).put(f"/dashboard/api/agents/{config_id}", data=self._agent_form(
            self.ids["project_b"], name="zz_ws_b_renamed"))
        self.assertEqual(renamed.status_code, 200, renamed.text)
        session = self.Session()
        for table, column in (("sessions", "app_name"), ("events", "app_name"),
                              ("lg_sessions", "app_name"), ("test_cases", "agent_name"),
                              ("response_feedback", "agent_name")):
            names = {row[0] for row in session.execute(text(f"SELECT {column} FROM {table}"))}
            self.assertNotIn(B_ROOT, names, table)
            self.assertIn("zz_ws_b_renamed", names, table)
        self.assertEqual(session.execute(text("SELECT content FROM events")).scalar(), B_MARKER)
        session.close()

    def _agent_form(self, project_id: int, name: str = "zz_ws_b_new", **fields) -> dict:
        return TestWorkspaceAdmins._agent_form(self, project_id, name, **fields)

    def test_a_name_with_rows_left_from_1_x_is_taken_outside_the_default_workspace(self):
        from shared.utils.tools import create_agent_tool
        session = self.Session()
        # Left by an agent renamed under 1.x: a LangGraph conversation. A rating keeps to
        # its project, and traces to their workspace (2k), so neither takes a name.
        session.add(LangGraphSession(id="lg-left", app_name="zz_left", user_id=ALICE))
        session.add(ResponseFeedback(session_id="s-r", message_id="m-r", agent_name="zz_rated",
                                     project_id=self.ids["project_b"], rating="down"))
        session.commit()
        session.close()
        bob = self._as(BOB)
        for name, expected in (("zz_left", 409), ("zz_rated", 200)):
            response = bob.post("/dashboard/api/agents", data=self._agent_form(self.ids["project_b"], name=name))
            self.assertEqual(response.status_code, expected, f"{name}: {response.text}")
        context = MagicMock()
        context.agent_name = B_ROOT
        with patch.object(create_agent_tool, "_is_admin_user", return_value=True):
            self.assertEqual(create_agent_tool.create_agent("zz_left", "d", "i", tool_context=context)["status"],
                             "error")
        # The default workspace takes it, as in 1.x
        response = self.client.post("/dashboard/api/agents", headers=_platform_admin(A),
                                    data=self._agent_form(self.ids["project_a"], name="zz_left"))
        self.assertEqual(response.status_code, 200, response.text)

    def test_ratings_of_an_agent_with_no_config_stay_in_the_default_workspace(self):
        session = self.Session()
        session.add(ResponseFeedback(session_id="s-h", message_id="m-h", agent_name="hardcoded_bot",
                                     rating="down", comment="HARDCODED"))
        session.commit()
        session.close()
        listed = self.client.get("/dashboard/api/evals/feedback", headers=_platform_admin(A))
        self.assertIn("HARDCODED", listed.text)
        self.assertNotIn("HARDCODED", self._as(BOB).get("/dashboard/api/evals/feedback").text)

    def test_the_replay_picker_sees_the_workspace_a_trace_recorded(self):
        session = self.Session()
        session.execute(text(
            "CREATE TABLE trace_spans (id INTEGER PRIMARY KEY, trace_id TEXT, span_id TEXT, "
            "parent_span_id TEXT, name TEXT, kind TEXT, start_time DATETIME, end_time DATETIME, "
            "duration_ms INTEGER, attributes TEXT, status TEXT, error_message TEXT)"))
        now = datetime.now()
        # The turn names B's agent; its inference span recorded workspace A
        for span, attributes in (("turn", {"gen_ai.agent.name": B_ROOT,
                                           "gen_ai.conversation.id": "session-of-a"}),
                                 ("inference", {"mate.workspace.id": A})):
            session.execute(text(
                "INSERT INTO trace_spans (trace_id, span_id, name, start_time, end_time, attributes) "
                "VALUES ('t', :s, :s, :st, :st, :a)"), {"s": span, "st": now, "a": json.dumps(attributes)})
        session.commit()
        session.close()
        response = self.client.get("/dashboard/api/traces/sessions", headers=_platform_admin(B))
        self.assertEqual(response.json()["sessions"], [])

    def test_the_built_in_account_is_a_platform_administrator_for_drive(self):
        from fastapi import FastAPI
        from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer
        app = FastAPI()
        GoogleDriveMCPServer(app, gdrive_mcp_available=True)
        # The built-in account whose row lacks the admin role: pat_auth counts it all the same
        session = self.Session()
        if not session.query(User).filter_by(user_id=auth.AUTH_USERNAME).update({"roles": '["user"]'}):
            session.add(User(user_id=auth.AUTH_USERNAME, roles='["user"]'))
        session.commit()
        session.close()
        response = TestClient(app).post("/gdrive/mcp/tools/list", headers=_platform_admin(B))
        self.assertEqual(response.status_code, 200)



class TestSixthReviewOfPhase2(_TwoWorkspaces):
    """The paths the sixth review of phase 2 found (#277, 2k)."""

    def test_a_runtime_sub_agent_works_for_the_agent_that_delegated_to_it(self):
        from types import SimpleNamespace
        from shared.utils import agent_config_identity
        from shared.utils.agent_config_identity import DELEGATING_AGENT
        from shared.utils.tools import create_agent_tool as tools
        servers = json.dumps({"mcpServers": {"s": {"command": "sh", "args": ["-c", "id"]}}})
        # Named <app>_<task>_<hex>, with no config: until 2k, the default workspace's
        sub_agent = SimpleNamespace(agent_name=f"{B_ROOT}_task_abc123")
        with patch.object(tools, "_is_admin_user", return_value=True), \
                patch.object(tools, "reload_agent_cache", return_value={"success": True}):
            token = DELEGATING_AGENT.set(B_ROOT)
            try:
                self.assertEqual(tools.update_agent("ws_a_sub", mcp_servers_config=servers,
                                                    tool_context=sub_agent)["status"], "error")
                self.assertEqual(tools.delete_agent(A_ROOT, tool_context=sub_agent)["status"], "error")
                refused = tools.update_agent("zz_ws_b_sub", mcp_servers_config=servers,
                                             tool_context=sub_agent)
                self.assertIn("platform administrator", refused["error_message"])
            finally:
                DELEGATING_AGENT.reset(token)
            # Any other name with no config, no recorded build and no code folder
            self.assertEqual(tools.update_agent("ws_a_sub", instruction="x",
                                                tool_context=sub_agent)["status"], "error")
            # A multi-parent instance works for the config it was built from
            agent_config_identity.record_loaded("zz_ws_b_sub_inst", {"hash": "h", "workspace_id": B})
            self.addCleanup(agent_config_identity._loaded.pop, "zz_ws_b_sub_inst", None)
            instance = SimpleNamespace(agent_name="zz_ws_b_sub_inst")
            self.assertEqual(tools.update_agent("ws_a_sub", instruction="x",
                                                tool_context=instance)["status"], "error")
            self.assertEqual(tools.update_agent("zz_ws_b_sub", instruction="x",
                                                tool_context=instance)["status"], "success")
            # Delegated by the default workspace's agent, as in 1.x
            token = DELEGATING_AGENT.set(A_ROOT)
            try:
                self.assertEqual(tools.update_agent("ws_a_sub", mcp_servers_config=servers,
                                                    tool_context=sub_agent)["status"], "success")
            finally:
                DELEGATING_AGENT.reset(token)
        session = self.Session()
        self.assertIsNotNone(session.query(AgentConfig).filter_by(name=A_ROOT).first())
        self.assertIsNone(session.query(AgentConfig).filter_by(name="zz_ws_b_sub").one().mcp_servers_config)
        session.close()

    def test_delegation_names_the_parent_while_a_sub_agent_runs(self):
        import asyncio
        from shared.utils.agent_config_identity import DELEGATING_AGENT
        from shared.utils.tools import subagent_delegation_tool as delegation
        seen = []

        def build(*args, **kwargs):
            seen.append(DELEGATING_AGENT.get())
            return [], []

        task = delegation.SubtaskSpec(name="t", instruction="i")
        with patch.object(delegation, "_build_subagent_tools", side_effect=build), \
                patch.object(delegation, "_resolve_subagent_model", side_effect=RuntimeError("stop")):
            result = asyncio.run(delegation._run_single_subagent(
                task, B_ROOT, BOB, "s", {"name": B_ROOT, "project_id": self.ids["project_b"]},
                None, 5.0, None))
        self.assertEqual(result["status"], "error")
        self.assertEqual(seen, [B_ROOT])
        self.assertIsNone(DELEGATING_AGENT.get())

    def test_a_slack_user_of_another_workspace_is_not_the_default_one_s(self):
        import asyncio
        from unittest.mock import AsyncMock
        from server import slack_routes
        self.assertEqual(slack_routes._slack_user_id("T1", "U1"), "slack_T1_U1")
        self.assertEqual(slack_routes._slack_user_id("T1", "U1", A), "slack_T1_U1")
        self.assertEqual(slack_routes._slack_user_id("T1", "U1", B), f"slack_w{B}_T1_U1")
        run = AsyncMock(return_value="")
        with patch.object(slack_routes, "run_agent_message", run):
            for agent in (B_ROOT, A_ROOT):
                asyncio.run(slack_routes._dispatch({"agent_name": agent, "team_id": None, "bot_token": "x"},
                                                   "C1", "U1", "s", None, "hi"))
        self.assertEqual([call.kwargs["user_id"] for call in run.await_args_list],
                         [f"slack_w{B}_t_U1", "slack_t_U1"])
        # A payload with no team id: not an integration that has one, and the oldest
        # of those that have none
        self.assertIsNone(slack_routes._get_integration(None))
        session = self.Session()
        session.add(ChannelIntegration(project_id=self.ids["project_a"], agent_name=A_ROOT, label="a"))
        session.add(ChannelIntegration(project_id=self.ids["project_b"], agent_name=B_ROOT, label="b"))
        session.commit()
        session.close()
        self.assertEqual(slack_routes._get_integration(None).agent_name, A_ROOT)
        self.assertEqual(slack_routes._get_integration("T_B").agent_name, B_ROOT)

    def test_spend_on_an_agent_or_project_is_its_own_workspace_s(self):
        from datetime import timedelta
        from shared.utils.token_usage_service import get_token_usage_service
        service = get_token_usage_service()
        since = datetime.now() - timedelta(days=1)
        # Spent in A under the name B's agent has now, by an agent deleted there since
        session = self.Session()
        session.add(TokenUsageLog(request_id="r-a", agent_name=B_ROOT, status="SUCCESS",
                                  prompt_tokens=500, response_tokens=500, cost_usd=5.0, workspace_id=A))
        session.commit()
        session.close()
        self.assertEqual(service.get_agent_tokens_since(B_ROOT, since), 2)
        self.assertEqual(service.get_agent_tokens_since(B_ROOT, since, include_sub_agents=True), 2)
        self.assertEqual(service.get_project_tokens_since(self.ids["project_b"], since), 2)
        self.assertEqual(service.get_spend_since("agent", B_ROOT, since)[0], 0.0)
        self.assertEqual(service.get_spend_since("project", str(self.ids["project_b"]), since)[0], 0.0)

    def test_a_rename_merges_what_is_left_under_the_new_name(self):
        from shared.utils.agent_cleanup import rename_agent_rows
        session = self.Session()
        session.execute(text(
            "CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT, state TEXT, "
            "create_time DATETIME, update_time DATETIME, PRIMARY KEY (app_name, user_id, id))"))
        session.execute(text(
            "CREATE TABLE events (id TEXT, app_name TEXT, user_id TEXT, session_id TEXT, content TEXT, "
            "PRIMARY KEY (id, app_name, user_id, session_id))"))
        session.execute(text("CREATE TABLE app_states (app_name TEXT PRIMARY KEY, state TEXT)"))
        session.execute(text("CREATE TABLE user_states (app_name TEXT, user_id TEXT, state TEXT, "
                             "PRIMARY KEY (app_name, user_id))"))
        for app, user, sid, event in (("zz_old", "u", "s1", "e-old"), ("zz_new", "u", "s1", "e-left"),
                                      ("zz_new", "u", "s2", "e-left-2")):
            session.execute(text("INSERT INTO sessions VALUES (:a, :u, :s, '{}', NULL, NULL)"),
                            {"a": app, "u": user, "s": sid})
            session.execute(text("INSERT INTO events VALUES (:e, :a, :u, :s, '')"),
                            {"e": event, "a": app, "u": user, "s": sid})
        session.execute(text("INSERT INTO app_states VALUES ('zz_old', 'old'), ('zz_new', 'left')"))
        session.execute(text("INSERT INTO user_states VALUES ('zz_old', 'u', 'old'), "
                             "('zz_new', 'u', 'left'), ('zz_new', 'v', 'left-v')"))
        # The agent, under its new name in the default workspace, and what 1.x left under it
        session.add(AgentConfig(name="zz_new", type="llm", project_id=self.ids["project_a"]))
        session.add(WidgetApiKey(api_key="wk_left", project_id=self.ids["project_a"], agent_name="zz_new"))
        # Ratings under the old name in both workspaces, and one under the new name in B
        for message, name, project in (("m1", "zz_old", "project_a"), ("m2", "zz_old", "project_b"),
                                       ("m3", "zz_new", "project_b")):
            session.add(ResponseFeedback(session_id="s-6", message_id=message, agent_name=name,
                                         project_id=self.ids[project], rating="down"))
        session.commit()
        rename_agent_rows(session, "zz_old", "zz_new")
        session.commit()
        ratings = dict(session.query(ResponseFeedback.message_id, ResponseFeedback.agent_name).filter(
            ResponseFeedback.session_id == "s-6").all())
        self.assertEqual(ratings, {"m1": "zz_new", "m2": "zz_old", "m3": "zz_new"})
        self.assertEqual(session.query(WidgetApiKey.agent_name).filter_by(api_key="wk_left").scalar(), "zz_new")
        self.assertEqual(sorted(session.execute(text("SELECT app_name, id FROM sessions")).all()),
                         [("zz_new", "s1"), ("zz_new", "s2")])
        self.assertEqual(sorted(session.execute(text("SELECT id, app_name FROM events")).all()),
                         [("e-left-2", "zz_new"), ("e-old", "zz_new")])
        self.assertEqual(session.execute(text("SELECT app_name, state FROM app_states")).all(),
                         [("zz_new", "old")])
        self.assertEqual(sorted(session.execute(text("SELECT user_id, state FROM user_states "
                                                     "WHERE app_name = 'zz_new'")).all()),
                         [("u", "old"), ("v", "left-v")])
        session.close()

    def test_a_trace_that_records_no_workspace_is_not_left_behind(self):
        from shared.utils.agent_cleanup import left_behind
        session = self.Session()
        session.execute(text(
            "CREATE TABLE trace_spans (id INTEGER PRIMARY KEY, trace_id TEXT, span_id TEXT, "
            "parent_span_id TEXT, name TEXT, kind TEXT, start_time DATETIME, end_time DATETIME, "
            "duration_ms INTEGER, attributes TEXT, status TEXT, error_message TEXT)"))
        session.execute(text(
            "INSERT INTO trace_spans (trace_id, span_id, name, start_time, end_time, attributes) "
            "VALUES ('old', 'a', 'invoke_agent', :now, :now, :a)"),
            {"now": datetime.now(), "a": json.dumps({"gen_ai.agent.name": "zz_traced",
                                                     "gen_ai.conversation.id": "old-session"})})
        session.commit()
        self.assertFalse(left_behind(session, "zz_traced"))
        session.close()
        # B takes the name, and doesn't see the default workspace's trace from before
        created = self.client.post("/dashboard/api/agents", headers=_platform_admin(B),
                                   data=TestWorkspaceAdmins._agent_form(self, self.ids["project_b"],
                                                                        "zz_traced"))
        self.assertEqual(created.status_code, 200, created.text)
        listed = self.client.get("/dashboard/api/traces", headers=_platform_admin(B)).json()["traces"]
        self.assertEqual(listed, [])
        listed = self.client.get("/dashboard/api/traces", headers=_platform_admin(A)).json()["traces"]
        self.assertEqual(listed, [])


class TestSeventhReviewOfPhase2(_TwoWorkspaces):
    """What the seventh review of phase 2 found (#277, 2l): none of it crosses a
    workspace."""

    _get = TestLogsAndOperations._get

    def _as(self, user_id: str) -> TestClient:
        return TestClient(auth_server.app, cookies=_signed_in(user_id))

    def test_the_rbac_check_reads_a_new_user_s_membership_afresh(self):
        # On MySQL the check's transaction would otherwise read as of its first query,
        # before get_or_create_user's session committed the membership
        from shared.utils import rbac_middleware
        events = []
        real_access = rbac_middleware.caller_access
        real_session = rbac_middleware.get_database_client().get_session

        def get_session() -> Session:
            session = real_session()
            rollback = session.rollback

            def logged_rollback() -> None:
                events.append("rollback")
                rollback()

            session.rollback = logged_rollback
            return session

        def access(session: Session, user: User, workspace_id: int) -> Optional[Tuple[bool, List[str]]]:
            events.append("access")
            return real_access(session, user, workspace_id)

        with patch.object(rbac_middleware.get_database_client(), "get_session", get_session), \
                patch.object(rbac_middleware, "caller_access", access):
            allowed, _ = rbac_middleware.RBACMiddleware().check_agent_access(
                "zz_new_user", {"name": B_ROOT, "allowed_for_roles": ["user"]})
        self.assertTrue(allowed)
        self.assertEqual(events[-2:], ["rollback", "access"])

    def test_an_import_naming_no_project_goes_to_the_workspace_s_own(self):
        bundle = {"agents": [{"name": "zz_imported", "type": "llm", "model_name": "m"}],
                  "memory_blocks": [{"label": "zz-imported-block", "value": "v"}]}
        result = self.client.post("/dashboard/api/agents/import", json=bundle,
                                  headers=_platform_admin(B)).json()
        self.assertEqual(result.get("imported_count"), 1, result)
        session = self.Session()
        self.assertEqual(session.query(AgentConfig).filter_by(name="zz_imported").one().project_id,
                         self.ids["project_b"])
        self.assertEqual(session.query(MemoryBlock).filter_by(label="zz-imported-block").one().project_id,
                         self.ids["project_b"])
        session.close()

    def test_a_renamed_parent_keeps_its_sub_agents(self):
        session = self.Session()
        # An agent of A naming B's root, as 1.x allowed: not B's to rename
        session.add(AgentConfig(name="ws_a_stray", type="llm", project_id=self.ids["project_a"],
                                parent_agents=json.dumps([B_ROOT, A_ROOT])))
        session.commit()
        config_id = self.b_rows["config_id"]
        session.close()
        renamed = self._as(BOB).put(f"/dashboard/api/agents/{config_id}", data=TestWorkspaceAdmins._agent_form(
            self, self.ids["project_b"], "zz_ws_b_renamed"))
        self.assertEqual(renamed.status_code, 200, renamed.text)
        session = self.Session()

        def parents(name: str) -> List[str]:
            return session.query(AgentConfig).filter_by(name=name).one().get_parent_agents()

        self.assertEqual(parents("zz_ws_b_sub"), ["zz_ws_b_renamed"])
        self.assertEqual(parents("ws_a_stray"), [B_ROOT, A_ROOT])
        session.close()

    def test_a_trigger_s_project_id_must_be_a_number(self):
        body = {"name": "t", "agent_name": B_ROOT, "prompt": "p", "trigger_type": "cron",
                "cron_expression": "0 * * * *", "output_type": "memory_block",
                "output_config": {"label": "x"}}
        for project_id in (None, "abc"):
            created = self.client.post("/dashboard/api/triggers", headers=_platform_admin(B),
                                       json={**body, "project_id": project_id})
            self.assertEqual(created.status_code, 400, created.text)
            updated = self.client.put(f"/dashboard/api/triggers/{self.b_rows['trigger_id']}",
                                      headers=_platform_admin(B), json={"project_id": project_id})
            self.assertEqual(updated.status_code, 400, updated.text)

    def test_the_dashboard_check_runs_off_the_event_loop(self):
        from server import request_context
        on_loop = []
        real = request_context.context_for

        def context_for(request: Request, user_id: str) -> request_context.RequestContext:
            on_loop.append(_on_event_loop())
            return real(request, user_id)

        with patch.object(request_context, "context_for", context_for):
            self._get("/dashboard/api/agents", B)
        self.assertEqual(on_loop[0], False)

    def test_the_playground_counts_the_workspace_s_cases(self):
        session = self.Session()
        session.add(TestCase(agent_name=B_ROOT, input="i", expected_output="o", workspace_id=A))
        session.commit()
        session.close()
        agent = self._get(f"/dashboard/api/evals/playground/agent/{B_ROOT}", B).json()
        self.assertEqual(agent["active_cases"], 1)


def _on_event_loop() -> bool:
    """True when called on a running asyncio event loop."""
    import asyncio
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class TestReviewOfPhase2l(_TwoWorkspaces):
    """What the review of 2l found (#277, 2m): none of it crosses a workspace."""

    _get = TestLogsAndOperations._get

    def test_a_parent_with_a_non_ascii_name_keeps_its_sub_agents(self):
        # json.dumps stores "pomoćnik" as "pomo\\u0107nik"
        session = self.Session()
        parent = AgentConfig(name="zz_pomoćnik", type="llm", project_id=self.ids["project_b"])
        session.add(parent)
        session.add(AgentConfig(name="zz_dete", type="llm", project_id=self.ids["project_b"],
                                parent_agents=json.dumps(["zz_pomoćnik"])))
        session.commit()
        parent_id = parent.id
        session.close()

        from shared.utils.agent_manager import AgentManager
        manager = AgentManager.__new__(AgentManager)
        manager.db_client = MagicMock(get_session=self.Session)
        self.assertEqual([a.name for a in manager.get_subagents("zz_pomoćnik")], ["zz_dete"])

        renamed = self.client.put(f"/dashboard/api/agents/{parent_id}", headers=_platform_admin(B),
                                  data=TestWorkspaceAdmins._agent_form(self, self.ids["project_b"],
                                                                       "zz_pomoćnik2"))
        self.assertEqual(renamed.status_code, 200, renamed.text)
        session = self.Session()
        self.assertEqual(session.query(AgentConfig).filter_by(name="zz_dete").one().get_parent_agents(),
                         ["zz_pomoćnik2"])
        session.close()
        self.assertEqual([a.name for a in manager.get_subagents("zz_pomoćnik2")], ["zz_dete"])

    def test_the_eval_pages_count_the_workspace_s_cases(self):
        session = self.Session()
        session.add(TestCase(agent_name=B_ROOT, input="i", expected_output="o", workspace_id=B))
        stray = TestCase(agent_name=B_ROOT, input="from A", expected_output="o", workspace_id=A)
        version = AgentConfigVersion(agent_config_id=self.b_rows["config_id"], version_number=1,
                                     config_snapshot=json.dumps({"name": B_ROOT}))
        session.add_all([stray, version])
        session.flush()
        session.add(EvalResult(test_case_id=stray.id, version_id=version.id, score=0.5,
                               passed=False, eval_method="exact_match"))
        session.commit()
        session.close()
        cases = self._get(f"/dashboard/api/evals/agent/{B_ROOT}", B).json()["test_cases"]
        self.assertIn("i", [case["input"] for case in cases])
        self.assertNotIn("from A", [case["input"] for case in cases])
        suites = self._get("/dashboard/api/evals", B).json()["suites"]
        self.assertEqual([suite["avg_score"] for suite in suites if suite["agent_name"] == B_ROOT], [None])
        history = self._get(f"/dashboard/api/evals/agent/{B_ROOT}/history", B).json()["history"]
        self.assertEqual(history, [])

    def test_a_platform_admin_s_workspace_is_resolved_off_the_event_loop(self):
        from server import request_context
        on_loop = []
        real = request_context.resolve_workspace

        def resolve_workspace(request: Request, user_id: str,
                              is_platform_admin: bool) -> Tuple[int, Optional[str]]:
            on_loop.append(_on_event_loop())
            return real(request, user_id, is_platform_admin)

        with patch.object(request_context, "resolve_workspace", resolve_workspace):
            self._get("/dashboard/api/agents", B)
        self.assertEqual(on_loop, [False])

    def test_a_denial_is_audited_off_the_event_loop(self):
        from shared.utils import audit_service
        on_loop = []

        def log(*args: Any, **kwargs: Any) -> None:
            on_loop.append(_on_event_loop())

        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE))
        with patch.object(audit_service, "log", log):
            denied = alice.post("/dashboard/api/projects", json={"name": "x"})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(on_loop, [False])

    def test_a_trigger_s_project_id_must_be_a_whole_number(self):
        body = {"name": "t", "agent_name": B_ROOT, "prompt": "p", "trigger_type": "cron",
                "cron_expression": "0 * * * *", "output_type": "memory_block",
                "output_config": {"label": "x"}}
        for project_id in (True, 2.5):
            created = self.client.post("/dashboard/api/triggers", headers=_platform_admin(B),
                                       json={**body, "project_id": project_id})
            self.assertEqual(created.status_code, 400, created.text)
        created = self.client.post("/dashboard/api/triggers", headers=_platform_admin(B),
                                   json={**body, "project_id": float(self.ids["project_b"])})
        self.assertEqual(created.status_code, 200, created.text)

    def test_an_import_naming_no_project_goes_to_the_oldest_without_project_1(self):
        session = self.Session()
        later = Project(name="zz-later", workspace_id=A)
        session.add(later)
        session.query(Project).filter_by(id=1).update({"workspace_id": B})
        session.commit()
        later_id = later.id
        session.close()
        result = self.client.post("/dashboard/api/agents/import", headers=_platform_admin(A),
                                  json={"agents": [{"name": "zz_imported", "type": "llm",
                                                    "model_name": "m"}]}).json()
        self.assertEqual(result.get("imported_count"), 1, result)
        session = self.Session()
        self.assertEqual(session.query(AgentConfig).filter_by(name="zz_imported").one().project_id,
                         later_id)
        session.close()


class TestAgentWithNoConfig(_TwoWorkspaces):
    """One rule for an agent name with no config, a hardcoded agent (#277, 3a): it is
    the default workspace's for a platform administrator, and no one else's. Carol
    administers the default workspace without the admin role, as phase 3 allows."""

    CAROL = "carol@a.example"
    NO_CONFIG = "zz_hardcoded_bot"

    def setUp(self):
        super().setUp()
        session = self.Session()
        session.add(User(user_id=self.CAROL, roles='["user"]'))
        session.flush()
        session.query(WorkspaceMember).filter_by(user_id=self.CAROL, workspace_id=A).update(
            {"role": "admin"})
        session.add(LangGraphSession(id="zz-hardcoded-session", app_name=self.NO_CONFIG,
                                     user_id=ALICE))
        session.commit()
        session.close()
        self.carol = TestClient(auth_server.app, cookies=_signed_in(self.CAROL))

    def _as_both(self, method: str, path: str, **kwargs) -> Tuple[int, int]:
        """(the platform administrator's status, carol's) for the same request in A."""
        with patch("shared.utils.audit_service.log"):
            admin = self.client.request(method, path, headers=_platform_admin(A), **kwargs)
            carol = self.carol.request(method, path, **kwargs)
        return admin.status_code, carol.status_code

    def test_the_rule(self):
        session = self.Session()
        try:
            self.assertEqual(agent_workspace_for(session, self.NO_CONFIG, True), A)
            self.assertIsNone(agent_workspace_for(session, self.NO_CONFIG, False))
            self.assertEqual(agent_workspace_for(session, B_ROOT, False), B)
            self.assertEqual(agents_outside(session, A, [self.NO_CONFIG, A_ROOT],
                                            is_platform_admin=False), [self.NO_CONFIG])
            self.assertEqual(agents_outside(session, A, [self.NO_CONFIG, A_ROOT],
                                            is_platform_admin=True), [])
        finally:
            session.close()

    def test_carol_administers_the_default_workspace(self):
        self.assertEqual(self.carol.get("/dashboard/api/agents").status_code, 200)

    def test_a_widget_key_or_slack_integration_targets_it_for_a_platform_admin_only(self):
        body = {"json": {"project_id": self.ids["project_a"], "agent_name": self.NO_CONFIG}}
        self.assertEqual(self._as_both("POST", "/dashboard/api/widget-keys", **body)[1], 404)
        admin, carol = self._as_both("POST", "/dashboard/api/channel-integrations", **body)
        self.assertEqual((admin, carol), (200, 404))

    def test_it_is_a_parent_for_a_platform_admin_only(self):
        def form(name: str) -> Dict[str, Any]:
            return {"data": {"name": name, "type": "llm", "project_id": str(self.ids["project_a"]),
                             "description": "d", "instruction": "i",
                             "parent_agents": json.dumps([self.NO_CONFIG])}}
        with patch("shared.utils.audit_service.log"):
            admin = self.client.post("/dashboard/api/agents", headers=_platform_admin(A),
                                     **form("zz_child_of_admin"))
            carol = self.carol.post("/dashboard/api/agents", **form("zz_child_of_carol"))
        self.assertEqual(admin.status_code, 200, admin.text)
        self.assertEqual((carol.status_code, carol.json()["detail"]),
                         (400, f"Not an agent of this workspace: {self.NO_CONFIG}"))

    def test_a_test_case_names_it_for_a_platform_admin_only(self):
        body = {"json": {"agent_name": self.NO_CONFIG, "input": "i", "expected_output": "o"}}
        self.assertEqual(self._as_both("POST", "/dashboard/api/evals", **body), (200, 404))

    def test_its_sessions_are_a_platform_admin_s_in_the_list_as_in_the_detail(self):
        from shared.utils.langgraph import session_store
        params = {"session_id": "zz-hardcoded-session", "app_name": self.NO_CONFIG,
                  "user_id": ALICE, "runtime": "langgraph"}
        with patch.object(session_store, "_session_store", None):
            listed = self.client.get("/dashboard/api/sessions", headers=_platform_admin(A))
            self.assertIn(self.NO_CONFIG, listed.text)
            self.assertNotIn(self.NO_CONFIG, self.carol.get("/dashboard/api/sessions").text)
            self.assertEqual(self.carol.get("/dashboard/api/sessions/detail", params=params)
                             .status_code, 404)


class TestClosedOrSuspendedWorkspace(_TwoWorkspaces):
    """A closed or suspended workspace stops everywhere, and the platform-only features
    follow workspaces.platform_features (#277, 3c). Phase 3c has no route that sets
    either column yet, so the tests set them directly."""

    def _set_b(self, **values) -> None:
        session = self.Session()
        session.query(Workspace).filter_by(id=B).update(values)
        session.commit()
        session.close()

    def _suspend_b(self) -> None:
        self._set_b(suspended_at=datetime.now(timezone.utc).replace(tzinfo=None))

    def test_no_one_uses_its_agents(self):
        rbac = RBACMiddleware()
        b_root = {"name": B_ROOT, "allowed_for_roles": ["user"]}
        self.assertTrue(rbac.check_agent_access(BOB, b_root)[0])
        for state in ({"suspended_at": datetime(2026, 11, 1)}, {"deleted_at": datetime(2026, 11, 1)}):
            with self.subTest(state=state):
                self._set_b(**{"suspended_at": None, "deleted_at": None, **state})
                self.assertFalse(rbac.check_agent_access(BOB, b_root)[0])
                session = self.Session()
                try:
                    admin = session.query(User).filter_by(user_id=BOB).one()
                    admin.set_roles(["admin"])  # a platform administrator, not stored
                    self.assertIsNone(caller_access(session, admin, B))
                finally:
                    session.rollback()
                    session.close()
                # Workspace A goes on
                self.assertTrue(rbac.check_agent_access(
                    ALICE, {"name": A_ROOT, "allowed_for_roles": ["user"]})[0])

    def test_its_dashboard_tokens_and_switcher_stop(self):
        from server.workspace_routes import workspaces_for
        self._suspend_b()
        response = self.client.get("/dashboard/api/agents", headers=_platform_admin(B))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(B, [w["id"] for w in workspaces_for(BOB, False)])
        self.assertNotIn(B, [w["id"] for w in workspaces_for(auth.AUTH_USERNAME, True)])
        bob = TestClient(auth_server.app, cookies=_signed_in(BOB))
        self.assertNotIn(B_ROOT, bob.get("/dashboard/api/agents").text)
        token = self.client.get("/v1/models", headers={"Authorization": f"Bearer {PATS[BOB]}"})
        self.assertEqual(token.status_code, 403)

    def test_its_widget_keys_slack_triggers_and_alert_rules_stop(self):
        from server import slack_routes
        service = AlertService()

        def measured() -> Set[int]:
            with patch.object(service, "_measure", return_value=None) as measure:
                service.evaluate_all()
            return {call.args[1].workspace_id for call in measure.call_args_list}

        self.assertIn(B, measured())
        self.assertEqual(self.client.get("/widget/public-config", params={"key": "wk_b"}).status_code, 200)
        self.assertIsNotNone(slack_routes._get_integration("T_B"))
        self._suspend_b()
        self.assertEqual(self.client.get("/widget/public-config", params={"key": "wk_b"}).status_code, 401)
        self.assertEqual(self.client.get("/widget/public-config", params={"key": "wk_a"}).status_code, 200)
        self.assertIsNone(slack_routes._get_integration("T_B"))

        session = self.Session()
        trigger = session.get(AgentTrigger, self.b_rows["trigger_id"])
        runner = TriggerRunner()
        with patch.object(runner, "_invoke_agent") as invoke:
            result = runner._execute_trigger_sync(trigger)
        session.close()
        invoke.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertNotIn(B, measured())

    def test_platform_features_follow_the_workspace(self):
        from shared.utils.platform_only import (
            FEATURE_CODE, FEATURE_EMAIL, FEATURE_SERVER_CREDENTIALS, may_use)
        features = (FEATURE_CODE, FEATURE_SERVER_CREDENTIALS, FEATURE_EMAIL)
        project_a, project_b = self.ids["project_a"], self.ids["project_b"]
        # The default workspace has all three, as 1.x; another one none
        self.assertTrue(all(may_use(f, A_ROOT, project_a) for f in features))
        self.assertFalse(any(may_use(f, B_ROOT, project_b) for f in features))
        # A runtime sub-agent with no config goes by its project
        self.assertFalse(may_use(FEATURE_CODE, "zz_ws_b_root_task_1", project_b))
        self._set_b(platform_features=json.dumps([FEATURE_CODE]))
        self.assertTrue(may_use(FEATURE_CODE, B_ROOT, project_b))
        self.assertFalse(may_use(FEATURE_EMAIL, B_ROOT, project_b))
        # Nor does a suspended workspace keep them
        self._suspend_b()
        self.assertFalse(may_use(FEATURE_CODE, B_ROOT, project_b))

    def test_email_follows_the_feature(self):
        rule = MagicMock(workspace_id=B, destination_type="email")
        rule.get_destination_config.return_value = {"to": "x@example.com"}
        with patch("shared.utils.alert_service.send_email", return_value=(True, "ok")) as sent:
            self.assertFalse(AlertService._deliver(MagicMock(), rule, {"message": "m"})[0])
            self._set_b(platform_features=json.dumps(["email"]))
            self.assertTrue(AlertService._deliver(MagicMock(), rule, {"message": "m"})[0])
        sent.assert_called_once()
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}), \
                patch("shared.utils.trigger_runner.send_email", return_value=(True, "ok")) as sent:
            TriggerRunner._output_email(MagicMock(), {"to": "x@example.com"}, "t",
                                        self.ids["project_b"], B_ROOT)
        sent.assert_called_once()



class TestPlatformWorkspaces(_TwoWorkspaces):
    """Platform → Workspaces (#277, 3d): a platform administrator creates, edits,
    suspends, resumes and closes workspaces, and every change is audited. Workspace
    owners are refused every route (TestEveryRoute, PLATFORM_ONLY)."""

    API = "/dashboard/api/platform/workspaces"

    def _call(self, method: str, path: str = "", **kwargs) -> httpx.Response:
        return self.client.request(method, self.API + path, headers=_platform_admin(A), **kwargs)

    def _audit(self, action: str) -> List[AuditLog]:
        session = self.Session()
        try:
            return session.query(AuditLog).filter_by(action=action).all()
        finally:
            session.close()

    def _workspace(self, workspace_id: int) -> Workspace:
        session = self.Session()
        try:
            return session.get(Workspace, workspace_id)
        finally:
            session.close()

    def _create(self, **body) -> httpx.Response:
        return self._call("POST", json={"name": "Carol's team", "slug": "carol", "owner": ALICE, **body})

    def test_a_new_workspace_gets_its_first_owner(self):
        response = self._create(platform_features=["email", "code"], allow_platform_keys=False)
        self.assertEqual(response.status_code, 200, response.text)
        created = response.json()["workspace"]
        self.assertEqual((created["status"], created["owners"], created["platform_features"]),
                         ("open", [ALICE], ["code", "email"]))
        session = self.Session()
        try:
            member = session.query(WorkspaceMember).filter_by(workspace_id=created["id"]).one()
            self.assertEqual((member.user_id, member.role, json.loads(member.agent_roles)),
                             (ALICE, "owner", ["user", "developer"]))
            self.assertFalse(session.get(Workspace, created["id"]).allow_platform_keys)
        finally:
            session.close()
        # alice may now open it, and owns it
        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE))
        listed = {w["id"]: w["role"] for w in alice.get("/dashboard/api/workspaces").json()["workspaces"]}
        self.assertEqual(listed.get(created["id"]), "owner")
        entry, = self._audit("workspace.create")
        self.assertEqual((entry.workspace_id, entry.resource_type, entry.resource_id),
                         (created["id"], "workspace", str(created["id"])))
        self.assertEqual(json.loads(entry.details)["owner"], ALICE)
        # The list holds every workspace, with counts
        listed = {w["id"]: w for w in self._call("GET").json()["workspaces"]}
        self.assertEqual(sorted(listed), [A, B, created["id"]])
        self.assertEqual((listed[B]["owners"], listed[B]["projects"]), ([BOB], 1))
        self.assertTrue(listed[A]["is_default"])

    def test_what_a_new_workspace_is_refused(self):
        session = self.Session()
        session.add_all([User(user_id="pending@x.example", roles='["pending"]'),
                         User(user_id="widget_1_abc", roles='["widget"]')])
        session.commit()
        session.close()
        for body, status in (({"slug": "Bad Slug"}, 400), ({"slug": "-carol"}, 400),
                             ({"slug": "c" * 41}, 400), ({"name": "  "}, 400),
                             ({"slug": B_MARKER}, 409), ({"name": "default"}, 409),
                             ({"owner": "nobody@x.example"}, 400), ({"owner": "pending@x.example"}, 400),
                             ({"owner": "widget_1_abc"}, 400), ({"owner": None}, 400),
                             ({"platform_features": ["root"]}, 400),
                             ({"allow_platform_keys": "yes"}, 400)):
            with self.subTest(body=body):
                self.assertEqual(self._create(**body).status_code, status)
        session = self.Session()
        try:
            self.assertEqual(session.query(Workspace).count(), 2)
        finally:
            session.close()
        self.assertEqual(self._audit("workspace.create"), [])

    def test_a_workspace_is_renamed_and_its_policy_set(self):
        response = self._call("PUT", f"/{B}", json={
            "name": "Renamed", "allow_platform_keys": False, "platform_features": ["code"]})
        self.assertEqual(response.json()["changed"],
                         ["allow_platform_keys", "name", "platform_features"])
        workspace = self._workspace(B)
        self.assertEqual((workspace.name, workspace.allow_platform_keys,
                          json.loads(workspace.platform_features)), ("Renamed", False, ["code"]))
        entry, = self._audit("workspace.update")
        self.assertEqual(entry.workspace_id, B)
        self.assertEqual(json.loads(entry.details)["name"], {"from": f"{B_MARKER} workspace", "to": "Renamed"})
        # The default workspace's name is taken, and an unchanged value changes nothing
        self.assertEqual(self._call("PUT", f"/{B}", json={"name": "DEFAULT"}).status_code, 409)
        self.assertEqual(self._call("PUT", f"/{B}", json={"name": "Renamed"}).json()["changed"], [])
        self.assertEqual(len(self._audit("workspace.update")), 1)
        self.assertEqual(self._call("PUT", f"/{B}", json={}).status_code, 400)
        self.assertEqual(self._call("PUT", "/99", json={"name": "x"}).status_code, 404)

    def test_suspending_stops_the_workspace_until_it_is_resumed(self):
        from server.workspace_routes import workspaces_for
        self.assertEqual(self._call("POST", f"/{B}/suspend").json()["status"], "suspended")
        self.assertIsNotNone(self._workspace(B).suspended_at)
        self.assertNotIn(B, [w["id"] for w in workspaces_for(BOB, False)])
        token = self.client.get("/v1/models", headers={"Authorization": f"Bearer {PATS[BOB]}"})
        self.assertEqual(token.status_code, 403)
        # Suspending again changes nothing
        self._call("POST", f"/{B}/suspend")
        self.assertEqual(len(self._audit("workspace.suspend")), 1)
        self.assertEqual(self._call("POST", f"/{B}/resume").json()["status"], "open")
        self.assertIn(B, [w["id"] for w in workspaces_for(BOB, False)])
        token = self.client.get("/v1/models", headers={"Authorization": f"Bearer {PATS[BOB]}"})
        self.assertEqual(token.status_code, 200)
        self.assertEqual([e.workspace_id for e in self._audit("workspace.resume")], [B])

    def test_the_default_workspace_is_never_suspended_or_closed(self):
        for verb in ("suspend", "close"):
            with self.subTest(verb=verb):
                self.assertEqual(self._call("POST", f"/{A}/{verb}").status_code, 409)
        # It keeps every platform feature, as in 1.x; its name can change
        self.assertEqual(self._call("PUT", f"/{A}", json={"platform_features": []}).status_code, 409)
        self.assertEqual(self._call("PUT", f"/{A}", json={
            "name": "Main", "platform_features": ["code", "server_credentials", "email"]}).json()["changed"],
            ["name"])
        workspace = self._workspace(A)
        self.assertIsNone(workspace.suspended_at)
        self.assertIsNone(workspace.deleted_at)
        self.assertEqual(json.loads(workspace.platform_features), ["code", "server_credentials", "email"])

    def test_only_a_workspace_with_no_projects_is_closed_and_for_good(self):
        response = self._call("POST", f"/{B}/close")
        self.assertEqual(response.status_code, 409)
        self.assertIn("1 project", response.json()["detail"])
        self.assertIsNone(self._workspace(B).deleted_at)
        empty = self._create().json()["workspace"]["id"]
        self.assertEqual(self._call("POST", f"/{empty}/close").json()["status"], "closed")
        self.assertIsNotNone(self._workspace(empty).deleted_at)
        self.assertEqual([e.workspace_id for e in self._audit("workspace.close")], [empty])
        for method, path in (("POST", f"/{empty}/resume"), ("POST", f"/{empty}/suspend"),
                             ("POST", f"/{empty}/close"), ("PUT", f"/{empty}")):
            with self.subTest(path=path):
                self.assertEqual(self._call(method, path, json={"name": "x"}).status_code, 409)
        listed = {w["id"]: w["status"] for w in self._call("GET").json()["workspaces"]}
        self.assertEqual(listed[empty], "closed")
        # It keeps its slug
        self.assertEqual(self._create(name="Other").status_code, 409)



class TestMembers(_TwoWorkspaces):
    """Workspace settings → Members (#277, 3e): owners change roles and agent roles and
    remove members, admins only look, anyone leaves, and a workspace keeps an owner.
    Agent roles live on the membership, and what they open follows it."""

    CAROL = "carol@b.example"

    def setUp(self):
        super().setUp()
        session = self.Session()
        carol = User(user_id=self.CAROL, roles='["user"]')
        carol.home_workspace_id = B
        session.add(carol)
        session.commit()
        session.close()
        self.bob = TestClient(auth_server.app, cookies=_signed_in(BOB))
        self.carol = TestClient(auth_server.app, cookies=_signed_in(self.CAROL))

    def _member(self, workspace_id: int, user_id: str) -> Optional[Tuple[str, List[str]]]:
        session = self.Session()
        try:
            row = session.query(WorkspaceMember).filter_by(workspace_id=workspace_id, user_id=user_id).first()
            return (row.role, json.loads(row.agent_roles)) if row else None
        finally:
            session.close()

    def _audit(self, action: str) -> List[AuditLog]:
        session = self.Session()
        try:
            return session.query(AuditLog).filter_by(action=action).all()
        finally:
            session.close()

    def test_an_owner_sees_their_workspace_s_members_only(self):
        listed = self.bob.get("/dashboard/api/members").json()
        self.assertEqual([m["user_id"] for m in listed["members"]], [BOB, self.CAROL])
        self.assertTrue(listed["can_manage"])
        self.assertEqual(listed["members"][1]["agent_roles"], ["user"])
        in_a = self.client.get("/dashboard/api/members", headers=_platform_admin(A)).json()
        self.assertNotIn(BOB, [m["user_id"] for m in in_a["members"]])
        self.assertIn(ALICE, [m["user_id"] for m in in_a["members"]])

    def test_an_owner_sets_a_member_s_role_and_agent_roles(self):
        response = self.bob.put(f"/dashboard/api/members/{self.CAROL}",
                                json={"role": "admin", "agent_roles": ["finance", " support", "finance"]})
        self.assertEqual(response.json()["changed"], ["agent_roles", "role"], response.text)
        self.assertEqual(self._member(B, self.CAROL), ("admin", ["finance", "support"]))
        entry, = self._audit("member.update")
        self.assertEqual((entry.workspace_id, entry.resource_id, entry.actor), (B, self.CAROL, BOB))
        self.assertEqual(json.loads(entry.details)["role"], {"from": "member", "to": "admin"})
        # Nothing changed, nothing audited
        self.assertEqual(self.bob.put(f"/dashboard/api/members/{self.CAROL}", json={"role": "admin"})
                         .json()["changed"], [])
        self.assertEqual(len(self._audit("member.update")), 1)

    def test_agent_roles_decide_which_agents_a_member_runs(self):
        rbac = RBACMiddleware()
        finance_only = {"name": B_ROOT, "allowed_for_roles": ["finance"]}
        self.assertFalse(rbac.check_agent_access(self.CAROL, finance_only)[0])
        self.bob.put(f"/dashboard/api/members/{self.CAROL}", json={"agent_roles": ["finance"]})
        self.assertTrue(rbac.check_agent_access(self.CAROL, finance_only)[0])
        self.assertFalse(rbac.check_agent_access(self.CAROL, {"name": B_ROOT, "allowed_for_roles": ["user"]})[0])

    def test_the_api_reads_the_agent_roles_of_the_token_s_workspace(self):
        models = lambda: self.client.get("/v1/models", headers={"Authorization": f"Bearer {PATS[ALICE]}"})
        self.assertEqual(models().status_code, 200)
        response = self.client.put(f"/dashboard/api/members/{ALICE}", headers=_platform_admin(A),
                                   json={"agent_roles": ["user"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(models().status_code, 403)

    def test_what_a_member_change_is_refused(self):
        for body in ({"role": "god"}, {"agent_roles": ["admin"]}, {"agent_roles": ["pending"]},
                     {"agent_roles": ["bad role!"]}, {"agent_roles": "user"}, {}):
            with self.subTest(body=body):
                self.assertEqual(self.bob.put(f"/dashboard/api/members/{self.CAROL}", json=body).status_code, 400)
        self.assertEqual(self.bob.put("/dashboard/api/members/nobody", json={"role": "member"}).status_code, 404)
        self.assertEqual(self._member(B, self.CAROL), ("member", ["user"]))

    def test_a_workspace_admin_looks_but_changes_nothing(self):
        self.bob.put(f"/dashboard/api/members/{self.CAROL}", json={"role": "admin"})
        self.assertEqual(self.carol.get("/dashboard/api/members").json()["can_manage"], False)
        self.assertEqual(self.carol.put(f"/dashboard/api/members/{BOB}", json={"role": "member"}).status_code, 403)
        self.assertEqual(self.carol.delete(f"/dashboard/api/members/{BOB}").status_code, 403)
        self.assertEqual(self._member(B, BOB)[0], "owner")

    def test_a_member_sees_no_member_list(self):
        self.assertEqual(self.carol.get("/dashboard/api/members").status_code, 403)
        self.assertIn(self.carol.get("/dashboard/members", follow_redirects=False).status_code, (302, 303, 307))

    def test_the_last_owner_stays(self):
        for response in (self.bob.put(f"/dashboard/api/members/{BOB}", json={"role": "member"}),
                         self.bob.delete(f"/dashboard/api/members/{BOB}"),
                         self.bob.post("/dashboard/api/members/leave")):
            with self.subTest(response=response.request.method):
                self.assertEqual(response.status_code, 409)
        self.assertEqual(self._member(B, BOB)[0], "owner")
        # With a second owner, the first may step down
        self.bob.put(f"/dashboard/api/members/{self.CAROL}", json={"role": "owner"})
        self.assertEqual(self.bob.put(f"/dashboard/api/members/{BOB}", json={"role": "member"}).status_code, 200)
        self.assertEqual(self.bob.put(f"/dashboard/api/members/{self.CAROL}", json={"role": "admin"}).status_code,
                         403)  # bob is no owner any more

    def test_an_owner_removes_a_member(self):
        self.assertEqual(self.bob.delete(f"/dashboard/api/members/{self.CAROL}").status_code, 200)
        self.assertIsNone(self._member(B, self.CAROL))
        entry, = self._audit("member.remove")
        self.assertEqual((entry.workspace_id, json.loads(entry.details)["role"]), (B, "member"))
        # The account stays
        session = self.Session()
        try:
            self.assertIsNotNone(session.query(User).filter_by(user_id=self.CAROL).first())
        finally:
            session.close()

    def test_a_member_leaves_the_current_workspace(self):
        from server.workspace_routes import workspaces_for
        self.assertEqual(self.carol.post("/dashboard/api/members/leave").status_code, 200)
        self.assertIsNone(self._member(B, self.CAROL))
        self.assertEqual(workspaces_for(self.CAROL, False), [])
        self.assertEqual([e.workspace_id for e in self._audit("member.leave")], [B])
        # Leaving acts on the current workspace only: alice stays in A
        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE))
        self.assertEqual(alice.post("/dashboard/api/members/leave").status_code, 200)
        self.assertIsNone(self._member(A, ALICE))
        self.assertIsNotNone(self._member(B, BOB))

    def test_the_users_page_sets_platform_roles_only(self):
        # The dashboard keeps the user service it started with
        users = patch.object(auth_server.dashboard_server, "user_service", UserService())
        users.start()
        self.addCleanup(users.stop)
        response = self.client.put(f"/dashboard/api/users/{ALICE}", headers=_platform_admin(A),
                                   data={"roles": json.dumps(["user", "developer"])})
        self.assertFalse(response.json()["success"])
        self.assertIn("Members", response.json()["message"])
        response = self.client.put(f"/dashboard/api/users/{ALICE}", headers=_platform_admin(A),
                                   data={"roles": json.dumps(["admin"])})
        self.assertTrue(response.json()["success"], response.text)
        # Her membership and agent roles stay as they were
        self.assertEqual(self._member(A, ALICE), ("member", ["user", "developer"]))


class TestInvitations(_TwoWorkspaces):
    """Invitations (#277, 3f): an owner invites an address with a role; the link works
    once, for 7 days, for whoever signs in with that address, and makes them a member of
    that workspace only. Revoking, replacing and every acceptance are audited."""

    DAVE = "dave@new.example"

    def setUp(self):
        super().setUp()
        self.bob = TestClient(auth_server.app, cookies=_signed_in(BOB))
        self.dave = TestClient(auth_server.app, cookies=_signed_in(self.DAVE))

    def _sign_up(self, user_id: str) -> None:
        """A first single sign-on, as the OAuth callback writes it: pending."""
        session = self.Session()
        session.add(User(user_id=user_id, email=user_id, roles='["pending"]'))
        session.commit()
        session.close()

    def _invite(self, email: str = DAVE, role: str = "admin", client: TestClient = None) -> str:
        response = (client or self.bob).post("/dashboard/api/invitations", json={"email": email, "role": role})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["link"].rsplit("/", 1)[1]

    def _accept(self, token: str, client: TestClient = None) -> httpx.Response:
        return (client or self.dave).post("/dashboard/api/invitations/accept", json={"token": token})

    def _rows(self, model: type, **filters) -> list:
        session = self.Session()
        try:
            return session.query(model).filter_by(**filters).all()
        finally:
            session.close()

    def test_an_owner_invites_to_the_current_workspace(self):
        response = self.bob.post("/dashboard/api/invitations", json={"email": " Dave@New.example ", "role": "admin"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["emailed"])
        self.assertTrue(body["link"].startswith("http://testserver/dashboard/invite/"))
        token = body["link"].rsplit("/", 1)[1]
        row, = self._rows(WorkspaceInvitation, email=self.DAVE)
        self.assertEqual((row.workspace_id, row.role, row.created_by), (B, "admin", BOB))
        # Only the token's hash is kept
        self.assertEqual(row.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(body["invitation"]))
        self.assertAlmostEqual((row.expires_at - row.created_at).days, 7)
        entry, = self._rows(AuditLog, action="invitation.create")
        self.assertEqual((entry.workspace_id, entry.actor, json.loads(entry.details)["email"]), (B, BOB, self.DAVE))
        listed = [i["email"] for i in self.bob.get("/dashboard/api/invitations").json()["invitations"]]
        self.assertIn(self.DAVE, listed)
        in_a = self.client.get("/dashboard/api/invitations", headers=_platform_admin(A)).json()["invitations"]
        self.assertEqual(in_a, [])

    def test_an_invitation_is_accepted_once(self):
        token = self._invite()
        self._sign_up(self.DAVE)
        page = self.dave.get(f"/dashboard/invite/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Join ", page.text)
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        response = self._accept(token)
        self.assertEqual(response.status_code, 200, response.text)
        member, = self._rows(WorkspaceMember, user_id=self.DAVE)
        self.assertEqual((member.workspace_id, member.role, json.loads(member.agent_roles), member.invited_by),
                         (B, "admin", ["user"], BOB))
        # No longer pending, and a member of B only: not of the default workspace
        user, = self._rows(User, user_id=self.DAVE)
        self.assertEqual(user.get_roles(), [])
        # The session works in B from now on
        signer = itsdangerous.TimestampSigner(str(auth_server._SECRET_KEY))
        cookie = json.loads(base64.b64decode(signer.unsign(response.cookies["session"])))
        self.assertEqual(cookie[SESSION_WORKSPACE_KEY], B)
        invitation, = self._rows(WorkspaceInvitation, email=self.DAVE)
        self.assertEqual(invitation.accepted_by, self.DAVE)
        entry, = self._rows(AuditLog, action="invitation.accept")
        self.assertEqual((entry.workspace_id, entry.actor), (B, self.DAVE))
        # Once
        self.assertEqual(self._accept(token).status_code, 410)
        self.assertIn("used already", self.dave.get(f"/dashboard/invite/{token}").text)
        self.assertEqual(len(self._rows(WorkspaceMember, user_id=self.DAVE)), 1)

    def test_only_the_invited_address_accepts(self):
        token = self._invite()
        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE))
        self.assertIn("sign in as dave@new.example", alice.get(f"/dashboard/invite/{token}").text)
        self.assertEqual(self._accept(token, alice).status_code, 403)
        self.assertEqual(self._rows(WorkspaceMember, workspace_id=B, user_id=ALICE), [])
        # A user whose email is the address, under another id, may
        session = self.Session()
        session.query(User).filter_by(user_id=ALICE).update({"email": self.DAVE})
        session.commit()
        session.close()
        self.assertEqual(self._accept(token, alice).status_code, 200)
        member, = self._rows(WorkspaceMember, workspace_id=B, user_id=ALICE)
        self.assertEqual(member.role, "admin")

    def test_an_expired_or_revoked_invitation_is_refused(self):
        self._sign_up(self.DAVE)
        token = self._invite()
        session = self.Session()
        session.query(WorkspaceInvitation).filter_by(email=self.DAVE).update(
            {"expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)})
        session.commit()
        session.close()
        self.assertEqual(self._accept(token).status_code, 410)
        self.assertIn("has expired", self.dave.get(f"/dashboard/invite/{token}").text)
        token = self._invite()
        invitation_id = self._rows(WorkspaceInvitation, email=self.DAVE)[0].id
        self.assertEqual(self.bob.delete(f"/dashboard/api/invitations/{invitation_id}").status_code, 200)
        self.assertEqual(self._accept(token).status_code, 404)
        self.assertEqual(self._rows(WorkspaceMember, user_id=self.DAVE), [])
        entry, = self._rows(AuditLog, action="invitation.revoke")
        self.assertEqual((entry.workspace_id, json.loads(entry.details)["email"]), (B, self.DAVE))

    def test_only_an_owner_invites_or_revokes(self):
        carol = "carol@b.example"
        session = self.Session()
        user = User(user_id=carol, roles='["user"]')
        user.home_workspace_id = B
        session.add(user)
        session.commit()
        session.close()
        self.bob.put(f"/dashboard/api/members/{carol}", json={"role": "admin"})
        carol_client = TestClient(auth_server.app, cookies=_signed_in(carol))
        invitation_id = self.b_rows["invitation_id"]
        listed = carol_client.get("/dashboard/api/invitations").json()
        self.assertFalse(listed["can_manage"])
        self.assertEqual(carol_client.post("/dashboard/api/invitations", json={"email": self.DAVE}).status_code, 403)
        self.assertEqual(carol_client.delete(f"/dashboard/api/invitations/{invitation_id}").status_code, 403)
        # A member of A: the middleware's 403
        alice = TestClient(auth_server.app, cookies=_signed_in(ALICE))
        self.assertEqual(alice.get("/dashboard/api/invitations").status_code, 403)
        self.assertEqual(len(self._rows(WorkspaceInvitation, workspace_id=B)), 1)

    def test_a_suspended_workspace_s_invitation_is_refused(self):
        self._sign_up(self.DAVE)
        token = self._invite()
        session = self.Session()
        session.query(Workspace).filter_by(id=B).update({"suspended_at": datetime.now(timezone.utc)})
        session.commit()
        session.close()
        self.assertEqual(self._accept(token).status_code, 404)
        self.assertIn("not valid", self.dave.get(f"/dashboard/invite/{token}").text)
        self.assertEqual(self._rows(WorkspaceMember, user_id=self.DAVE), [])

    def test_what_an_invitation_refuses(self):
        for body in ({"email": "not an address"}, {"email": self.DAVE, "role": "god"}, {}):
            with self.subTest(body=body):
                self.assertEqual(self.bob.post("/dashboard/api/invitations", json=body).status_code, 400)
        # A member, by id or by email
        self.assertEqual(self.bob.post("/dashboard/api/invitations", json={"email": BOB}).status_code, 409)
        # Inviting again replaces the open invitation: the first link stops working
        self._sign_up(self.DAVE)
        first = self._invite()
        second = self._invite(role="member")
        self.assertEqual(len(self._rows(WorkspaceInvitation, email=self.DAVE)), 1)
        self.assertEqual(self._accept(first).status_code, 404)
        self.assertEqual(self._accept(second).status_code, 200)
        # Already a member: not used up
        token = self._invite(email="other@new.example")
        session = self.Session()
        session.query(User).filter_by(user_id=self.DAVE).update({"email": "other@new.example"})
        session.commit()
        session.close()
        self.assertEqual(self._accept(token).status_code, 409)
        self.assertIsNone(self._rows(WorkspaceInvitation, email="other@new.example")[0].accepted_at)
        for token in ("", "x" * 43, None):
            self.assertEqual(self._accept(token).status_code, 404)

    def test_the_link_is_emailed_when_smtp_is_set(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example"}), \
                patch("shared.utils.notify.send_email", return_value=(True, "sent")) as send:
            response = self.bob.post("/dashboard/api/invitations", json={"email": self.DAVE})
        self.assertTrue(response.json()["emailed"])
        to, subject, text = send.call_args.args
        self.assertEqual(to, self.DAVE)
        self.assertIn(B_MARKER, subject)
        self.assertIn(response.json()["link"], text)

    def test_a_workspace_is_created_with_an_invited_owner(self):
        api = "/dashboard/api/platform/workspaces"
        both = {"name": "Dave's team", "slug": "dave", "owner": ALICE, "owner_email": self.DAVE}
        self.assertEqual(self.client.post(api, headers=_platform_admin(A), json=both).status_code, 400)
        del both["owner"]
        response = self.client.post(api, headers=_platform_admin(A), json=both)
        self.assertEqual(response.status_code, 200, response.text)
        created = response.json()["workspace"]
        self.assertEqual((created["owners"], created["invited_owners"], created["members"]), ([], [self.DAVE], 0))
        listed = {w["id"]: w for w in self.client.get(api, headers=_platform_admin(A)).json()["workspaces"]}
        self.assertEqual(listed[created["id"]]["invited_owners"], [self.DAVE])
        self.assertEqual(json.loads(self._rows(AuditLog, action="workspace.create")[0].details)["owner_invited"],
                         self.DAVE)
        self._sign_up(self.DAVE)
        token = response.json()["invitation"]["link"].rsplit("/", 1)[1]
        self.assertEqual(self._accept(token).status_code, 200)
        member, = self._rows(WorkspaceMember, user_id=self.DAVE)
        self.assertEqual((member.workspace_id, member.role), (created["id"], "owner"))

    def test_signing_in_comes_back_to_the_invitation(self):
        response = TestClient(auth_server.app).get("/dashboard/invite/abc", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("/login?redirect=%2Fdashboard%2Finvite%2Fabc", response.headers["location"])
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret"}):
            page = self.client.get("/login", params={"redirect": "/dashboard/invite/abc"}).text
            self.assertIn('href="/auth/login/google?next=%2Fdashboard%2Finvite%2Fabc"', page)
            # Only to a page of this site
            page = self.client.get("/login", params={"redirect": "//evil.example/x"}).text
            self.assertIn('href="/auth/login/google"', page)


class TestWorkspaceBudgets(_TwoWorkspaces):
    """Workspace budgets and alerts (#277, 3g): a budget of scope workspace counts every
    agent of its workspace and no other, and a whole-workspace (global) alert rule
    measures it."""

    def setUp(self):
        super().setUp()
        self.bob = TestClient(auth_server.app, cookies=_signed_in(BOB))

    def _use(self, workspace_id: int, agent: str, tokens: int, cost: float = None) -> None:
        session = self.Session()
        session.add(TokenUsageLog(request_id="r", session_id="s", user_id="someone", agent_name=agent,
                                  model_name="m", status="SUCCESS", prompt_tokens=tokens, response_tokens=0,
                                  cost_usd=cost, workspace_id=workspace_id,
                                  timestamp=datetime.now(timezone.utc)))
        session.commit()
        session.close()

    # B's root has a budget of its own in the fixture; its sub-agent has none
    B_SUB = "zz_ws_b_sub"

    def _check(self, user_id: str, agent: str):
        return asyncio.run(rate_limit_service.get_rate_limit_service().check_request_limit(user_id, agent))[0]

    def test_a_workspace_budget_counts_every_agent_of_its_workspace(self):
        response = self.bob.post("/dashboard/api/rate-limits", json={
            "scope": "workspace", "scope_id": "1", "tokens_per_day": 100, "action_on_limit": "block"})
        self.assertEqual(response.status_code, 200, response.text)
        session = self.Session()
        try:
            config = session.query(RateLimitConfig).filter_by(scope="workspace").one()
            # The caller's workspace, whatever scope_id said
            self.assertEqual((config.scope_id, config.workspace_id), (str(B), B))
        finally:
            session.close()
        # A's use is not B's
        self._use(A, A_ROOT, 10_000)
        self.assertTrue(self._check(BOB, self.B_SUB).allowed)
        # Another agent's use, by another user: only the workspace's count sees it
        self._use(B, B_ROOT, 150)
        result = self._check("anyone@b.example", self.B_SUB)
        self.assertFalse(result.allowed)
        self.assertIn("Workspace budget exceeded", result.message)
        # and B's budget is not A's
        self.assertTrue(self._check(ALICE, A_ROOT).allowed)

    def test_a_workspace_budget_in_dollars(self):
        self.bob.post("/dashboard/api/rate-limits", json={"scope": "workspace", "usd_per_day": 1.0})
        self._use(A, A_ROOT, 1, cost=50.0)
        self.assertTrue(self._check(BOB, self.B_SUB).allowed)
        self._use(B, B_ROOT, 1, cost=1.5)
        result = self._check(BOB, self.B_SUB)
        self.assertFalse(result.allowed)
        self.assertIn("Workspace budget exceeded: $1.50", result.message)

    def test_a_workspace_budget_has_no_per_caller_limits(self):
        for key in ("requests_per_minute", "max_tokens_per_request"):
            response = self.bob.post("/dashboard/api/rate-limits",
                                     json={"scope": "workspace", key: 10, "tokens_per_day": 1})
            self.assertEqual(response.status_code, 400, key)

    def test_the_usage_of_the_whole_workspace(self):
        self._use(A, A_ROOT, 1000, cost=3.0)
        self._use(B, B_ROOT, 70, cost=0.25)
        usage = self.bob.get("/dashboard/api/rate-limits/usage", params={"workspace": "true"}).json()
        # The fixture's log of B holds 2 tokens
        self.assertEqual((usage["tokens_last_day"], usage["usd_last_day"]), (72, 0.25))
        usage = self.client.get("/dashboard/api/rate-limits/usage", params={"workspace": "true"},
                                headers=_platform_admin(A)).json()
        self.assertEqual((usage["tokens_last_day"], usage["usd_last_day"]), (1000, 3.0))

    def test_only_the_workspace_deletes_its_budget(self):
        self.bob.post("/dashboard/api/rate-limits", json={"scope": "workspace", "tokens_per_day": 5})
        response = self.client.delete(f"/dashboard/api/rate-limits/workspace/{B}", headers=_platform_admin(A))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.bob.delete(f"/dashboard/api/rate-limits/workspace/{B}").status_code, 200)

    def test_a_whole_workspace_alert_measures_its_budget(self):
        self.bob.post("/dashboard/api/rate-limits", json={"scope": "workspace", "usd_per_month": 2.0,
                                                          "tokens_per_day": 1000})
        self._use(A, A_ROOT, 5000, cost=100.0)
        self._use(B, B_ROOT, 498, cost=1.0)
        service = alert_service.AlertService()
        session = self.Session()
        try:
            for config, value in (({"unit": "usd", "period": "month"}, 50),
                                  ({"period": "day"}, 50)):
                rule = AlertRule(name="r", scope="global", condition_type="budget_threshold",
                                 destination_type="http", workspace_id=B,
                                 condition_config=json.dumps(config))
                measured = service._measure(session, rule)
                self.assertEqual(measured["value"], value, config)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
