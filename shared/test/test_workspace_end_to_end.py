#!/usr/bin/env python3
"""
Phase 3 of the workspaces (#277), end to end: a new workspace is set up from the
dashboard alone, and a suspended one stops (documents/design/2.0-plan.md, 3i).

Everything the test does goes through HTTP, as a platform administrator, an owner
and a member would: Platform → Workspaces, single sign-on (against a fake provider),
the invitation page, Members, Projects, Agents, Rate Limits, personal access tokens,
widget keys, Slack, webhook triggers, and the agent routes behind the proxy (against a
fake agent server). The fixture's database starts with the two workspaces of
test_cross_workspace.py, which the new one must not touch; the test reads nothing
from it directly.
"""

import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from shared.test.test_cross_workspace import (
    A, A_ROOT, B_MARKERS, _TwoWorkspaces, _platform_admin, auth_server,
)
from server import oauth_routes
from server.request_context import WORKSPACE_HEADER
from shared.utils.trigger_runner import TriggerRunner

OWNER = "owner@acme.example"
MEMBER = "member@acme.example"
AGENT = "acme_support_root"
TEAM = "T_ACME"
SLACK_SECRET = "acme-signing-secret"


class _FakeProvider:
    """A Google sign-in that always answers with one address."""

    def __init__(self, email: str):
        self.email = email

    async def authorize_access_token(self, request):
        return {"userinfo": {"email": self.email, "name": self.email.split("@")[0]}}


class TestPhase3EndToEnd(_TwoWorkspaces):

    def setUp(self):
        super().setUp()
        self.admin = TestClient(auth_server.app)
        self.reached: List[str] = []

        def agent_server(request: httpx.Request) -> httpx.Response:
            self.reached.append(request.url.path)
            if request.url.path == "/list-apps":
                return httpx.Response(200, json=[AGENT, A_ROOT])
            return httpx.Response(200, json={"id": "s1"})

        transport = httpx.MockTransport(agent_server)
        real_client = httpx.AsyncClient
        for patcher in (
            patch.object(httpx, "AsyncClient",
                         lambda *a, **kw: real_client(transport=transport, timeout=kw.get("timeout"))),
            # The agent behind a trigger, as the agent server would answer
            patch.object(TriggerRunner, "_invoke_agent", return_value="done"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ #
    # How each person gets in                                             #
    # ------------------------------------------------------------------ #

    def _sign_in(self, email: str) -> TestClient:
        """A first single sign-on, through the OAuth callback."""
        client = TestClient(auth_server.app)
        with patch.object(oauth_routes, "_get_oauth",
                          return_value=SimpleNamespace(google=_FakeProvider(email))):
            response = client.get("/auth/callback/google", follow_redirects=False)
        self.assertEqual(response.status_code, 307, response.text)
        self.assertEqual(response.headers["location"], "/dashboard")
        return client

    def _ok(self, response: httpx.Response, status: int = 200) -> dict:
        self.assertEqual(response.status_code, status, response.text[:300])
        try:
            return response.json()
        except ValueError:
            return {}

    def _token(self, link: str) -> str:
        return link.rsplit("/", 1)[1]

    def _accept(self, client: TestClient, link: str) -> int:
        page = client.get(f"/dashboard/invite/{self._token(link)}")
        self.assertIn("Join ", page.text)
        return self._ok(client.post("/dashboard/api/invitations/accept",
                                    json={"token": self._token(link)}))["workspace_id"]

    def _slack(self, event_type: str = "reaction_added") -> int:
        body = json.dumps({"type": "event_callback", "team_id": TEAM, "event_id": f"Ev{time.time_ns()}",
                           "event": {"type": event_type}}).encode()
        timestamp = str(int(time.time()))
        signature = "v0=" + hmac.new(SLACK_SECRET.encode(), f"v0:{timestamp}:".encode() + body,
                                     hashlib.sha256).hexdigest()
        return self.admin.post("/integrations/slack/events", content=body, headers={
            "Content-Type": "application/json", "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature}).status_code

    # ------------------------------------------------------------------ #
    # The test                                                            #
    # ------------------------------------------------------------------ #

    def test_a_workspace_is_set_up_from_the_dashboard_and_stops_when_suspended(self):
        # 1. The platform administrator creates the workspace and invites its owner
        created = self._ok(self.admin.post("/dashboard/api/platform/workspaces", headers=_platform_admin(A),
                                           json={"name": "Acme", "slug": "acme", "owner_email": OWNER}))
        workspace_id = created["workspace"]["id"]
        self.assertEqual(created["workspace"]["invited_owners"], [OWNER])

        # 2. The owner signs in for the first time, pending, and accepts
        owner = self._sign_in(OWNER)
        self.assertNotIn(workspace_id, [w["id"] for w in owner.get("/dashboard/api/workspaces").json()["workspaces"]])
        self.assertEqual(self._accept(owner, created["invitation"]["link"]), workspace_id)
        listed = owner.get("/dashboard/api/workspaces").json()
        self.assertEqual(([w["id"] for w in listed["workspaces"]], listed["current"]),
                         ([workspace_id], workspace_id))
        members = self._ok(owner.get("/dashboard/api/members"))
        self.assertEqual([(m["user_id"], m["role"]) for m in members["members"]], [(OWNER, "owner")])

        # 3. The owner creates a project and an agent open to the role "support"
        project_id = self._ok(owner.post("/dashboard/api/projects", data={"name": "Support"}))["project"]["id"]
        self._ok(owner.post("/dashboard/api/agents", data={
            "name": AGENT, "type": "llm", "project_id": str(project_id), "model_name": "gemini/gemini-2.5-flash",
            "description": "Answers customers", "instruction": "Help.",
            "allowed_for_roles": json.dumps(["support"])}))
        agents = [a["name"] for a in self._ok(owner.get("/dashboard/api/agents"))["configs"]]
        self.assertEqual(agents, [AGENT])
        # None of the other workspaces' agents
        self.assertFalse(any(m in json.dumps(agents) for m in B_MARKERS + (A_ROOT,)))

        # 4. The owner invites a member, who accepts; the owner gives them agent roles
        link = self._ok(owner.post("/dashboard/api/invitations",
                                   json={"email": MEMBER, "role": "member"}))["link"]
        member = self._sign_in(MEMBER)
        self.assertEqual(self._accept(member, link), workspace_id)
        # Before the agent role, the agent's roles keep them out
        self.assertEqual(member.post(f"/a2a/{AGENT}", json={}).status_code, 403)
        self._ok(owner.put(f"/dashboard/api/members/{MEMBER}", json={"agent_roles": ["support", "developer"]}))

        # 5. The owner sets a budget for the whole workspace
        self._ok(owner.post("/dashboard/api/rate-limits", json={
            "scope": "workspace", "usd_per_month": 50, "tokens_per_day": 1_000_000}))
        budget, = self._ok(owner.get("/dashboard/api/rate-limits"))["configs"]
        self.assertEqual((budget["scope"], budget["scope_id"], budget["usd_per_month"]),
                         ("workspace", str(workspace_id), 50))
        self._ok(owner.get("/dashboard/api/rate-limits/usage", params={"workspace": "true"}))

        # ... and the channels the agent answers on
        widget_key = self._ok(owner.post("/dashboard/api/widget-keys", json={
            "project_id": project_id, "agent_name": AGENT}))["key"]["api_key"]
        self._ok(owner.post("/dashboard/api/channel-integrations", json={
            "project_id": project_id, "agent_name": AGENT, "team_id": TEAM,
            "bot_token": "xoxb-acme", "signing_secret": SLACK_SECRET}))
        trigger = self._ok(owner.post("/dashboard/api/triggers", json={
            "name": "nightly", "agent_name": AGENT, "project_id": project_id,
            "trigger_type": "webhook", "prompt": "Report"}))
        fire = (f"/triggers/{trigger['trigger']['id']}/fire", {"X-MATE-Trigger-Key": trigger["fire_key"]})

        # 6. The member runs the agent: in the Work Room, over A2A, and with a token
        self.assertEqual(member.get("/list-apps").json(), [AGENT])
        self.assertIn(AGENT, member.get("/dashboard/workroom").text)
        run = {"app_name": AGENT, "user_id": MEMBER, "session_id": "s1",
               "new_message": {"role": "user", "parts": [{"text": "Hello"}]}}
        self._ok(member.post("/run_sse", json=run))
        self._ok(member.post(f"/a2a/{AGENT}", json={}))
        self.assertIn("/run_sse", self.reached)
        pat = self._ok(member.post("/dashboard/api/tokens", json={"name": "cli"}))["token"]
        api = {"Authorization": f"Bearer {pat}"}
        self._ok(self.admin.get("/v1/models", headers=api))
        self._ok(self.admin.get("/widget/public-config", params={"key": widget_key}))
        self.assertEqual(self._slack(), 200)
        self.assertEqual(self._ok(self.admin.post(fire[0], headers=fire[1]))["status"], "ok")

        # The platform administrator sees it among the workspaces, and its audit trail
        listed = {w["id"]: w for w in self._ok(self.admin.get(
            "/dashboard/api/platform/workspaces", headers=_platform_admin(A)))["workspaces"]}
        self.assertEqual((listed[workspace_id]["owners"], listed[workspace_id]["members"],
                          listed[workspace_id]["projects"]), ([OWNER], 2, 1))
        actions = {log["action"] for log in self._ok(self.admin.get(
            "/dashboard/api/audit-logs", headers=_platform_admin(workspace_id)))["logs"]}
        self.assertTrue({"workspace.create", "invitation.create", "invitation.accept",
                         "member.update"} <= actions, actions)

        # 7. Suspended, every entry point stops
        self._ok(self.admin.post(f"/dashboard/api/platform/workspaces/{workspace_id}/suspend",
                                 headers=_platform_admin(A)))
        stopped: Dict[str, int] = {
            "dashboard": owner.get("/dashboard/api/agents", headers={WORKSPACE_HEADER: str(workspace_id)}).status_code,
            "run_sse": member.post("/run_sse", json=run).status_code,
            "a2a": member.post(f"/a2a/{AGENT}", json={}).status_code,
            "api": self.admin.get("/v1/models", headers=api).status_code,
            "widget": self.admin.get("/widget/public-config", params={"key": widget_key}).status_code,
            "slack": self._slack(),
            "invitation": owner.post("/dashboard/api/invitations/accept", json={"token": "x" * 43}).status_code,
        }
        self.assertEqual(stopped, {"dashboard": 404, "run_sse": 403, "a2a": 403, "api": 403,
                                   "widget": 401, "slack": 404, "invitation": 404})
        self.assertEqual(self._ok(self.admin.post(fire[0], headers=fire[1]))["status"], "skipped")
        # Nor does the member fall back to another workspace's agents
        self.assertEqual(member.get("/list-apps").json(), [])
        workroom = member.get("/dashboard/workroom").text
        self.assertFalse(any(name in workroom for name in (AGENT, A_ROOT)))
        self.assertNotIn(AGENT, owner.get("/dashboard/api/agents").text)
        self.assertEqual(owner.get("/dashboard/api/workspaces").json()["workspaces"], [])

        # Resumed, it goes on
        self._ok(self.admin.post(f"/dashboard/api/platform/workspaces/{workspace_id}/resume",
                                 headers=_platform_admin(A)))
        self._ok(member.post("/run_sse", json=run))
        self._ok(self.admin.get("/v1/models", headers=api))


if __name__ == "__main__":
    unittest.main()
