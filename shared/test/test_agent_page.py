"""
The agent page: /dashboard/agents/<name>, /dashboard/agents/new and its Copy
(?from=<name>), on the two workspaces of test_cross_workspace (dashboard-2.1.md,
"The agent page").
"""

import json
import re
import unittest
from datetime import datetime

from sqlalchemy import text

from shared.test.test_cross_workspace import (
    A, A_ROOT, B, B_MARKERS, B_ROOT, BOB, _platform_admin, _signed_in, _TwoWorkspaces,
)
from shared.utils.models import AgentConfig, LangGraphSession, TokenUsageLog, WorkspaceMember
from shared.utils.workspaces import roles_in_use

import auth_server
from fastapi.testclient import TestClient


def _page_data(html: str) -> dict:
    match = re.search(r'<script id="agent-page-data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(match.group(1))


class TestAgentPage(_TwoWorkspaces):

    def _get(self, path: str, workspace_id: int = A):
        return self.client.get(path, headers=_platform_admin(workspace_id))

    def test_an_agent_s_page_shows_its_settings(self):
        response = self._get(f"/dashboard/agents/{A_ROOT}")
        self.assertEqual(response.status_code, 200)
        data = _page_data(response.text)
        self.assertEqual((data["mode"], data["config"]["name"]), ("edit", A_ROOT))
        self.assertIn('id="agentForm"', response.text)
        # A root agent can be tested in the Work Room
        self.assertIn(f'/dashboard/workroom?agent={A_ROOT}', response.text)
        # The page, not base.html's workspace switcher, which lists every workspace
        page = response.text[response.text.index('id="agentPage"'):]
        for marker in B_MARKERS:
            self.assertNotIn(marker, page)

    def test_the_sections_hold_the_configuration_fields(self):
        html = self._get(f"/dashboard/agents/{A_ROOT}").text
        form = html[html.index('<form id="agentForm"'):html.index("</form>")]
        for field in ("agentToolConfig", "agentGoogleDrive", "agentMcpServersConfig", "agentPlannerType",
                      "agentTemperature", "agentInputSchema", "agentOutputSchema", "agentGrPii",
                      "agentGuardrailConfig"):
            self.assertEqual(html.count(f'id="{field}"'), 1, field)
            self.assertIn(f'id="{field}"', form)
        # No dialog holds them a second time
        self.assertNotIn("agentToolConfigModal", html)

    def test_the_visual_builder_keeps_its_dialogs(self):
        html = self._get(f"/dashboard/agents/visual?project_id={self.ids['project_a']}").text
        for dialog in ("vbToolConfigModal", "vbGuardrailConfigModal", "vbPlannerConfigModal"):
            self.assertIn(f'id="{dialog}"', html)
        self.assertEqual(html.count('id="vbGrPii"'), 1)

    def test_an_agent_has_versions_and_sessions_tabs(self):
        html = self._get(f"/dashboard/agents/{A_ROOT}").text
        for tab in ("tab-settings", "tab-versions", "tab-sessions"):
            self.assertIn(f'id="{tab}"', html)
        self.assertIn('id="vhVersionList"', html)
        self.assertEqual(_page_data(html)["apps"], [A_ROOT])
        # A new agent has nothing to show in them yet
        html = self._get(f"/dashboard/agents/new?project_id={self.ids['project_a']}").text
        self.assertNotIn('id="tab-versions"', html)
        self.assertNotIn('id="tab-sessions"', html)

    def test_a_sub_agent_s_sessions_are_its_root_s(self):
        html = self._get("/dashboard/agents/ws_a_sub").text
        self.assertEqual(_page_data(html)["apps"], [A_ROOT])
        self.assertIn(f"those of the root agent it works under, {A_ROOT}", html)

    def test_the_sessions_tab_asks_for_the_app_exactly(self):
        session = self.Session()
        session.add(AgentConfig(name=f"{A_ROOT}_x", type="llm", project_id=self.ids["project_a"]))
        session.add_all([LangGraphSession(id="s-root", app_name=A_ROOT, user_id="u"),
                         LangGraphSession(id="s-root-x", app_name=f"{A_ROOT}_x", user_id="u")])
        session.commit()
        session.close()
        ids = lambda **params: {s["id"] for s in self._get_json("/dashboard/api/sessions", app_name=A_ROOT,
                                                                 runtime="langgraph", **params)["sessions"]}
        self.assertEqual(ids(), {"s-root", "s-root-x"})
        self.assertEqual(ids(exact_app="true"), {"s-root"})

    def test_the_tabs_of_an_agent(self):
        html = self._get(f"/dashboard/agents/{A_ROOT}").text
        for tab in ("tab-evals", "tab-usage", "tab-traces"):
            self.assertIn(f'id="{tab}"', html)
        self.assertIn(f'href="/dashboard/evals?agent={A_ROOT}"', html)

    def test_usage_of_one_agent_with_totals_per_model(self):
        session = self.Session()
        now = datetime.now()
        for agent, model, tokens, cost in ((A_ROOT, "m1", 10, 0.5), (A_ROOT, "m1", 5, 0.25),
                                           (A_ROOT, "m2", 7, None), ("ws_a_sub", "m1", 100, 9.0)):
            session.add(TokenUsageLog(request_id=f"r-{agent}-{tokens}", agent_name=agent, model_name=model,
                                      prompt_tokens=tokens, response_tokens=1, cost_usd=cost,
                                      timestamp=now, workspace_id=A))
        session.commit()
        session.close()
        result = self._get_json("/dashboard/api/usage/logs", agent_name=A_ROOT, hours=24)
        self.assertEqual(result["total_records"], 3)
        self.assertEqual({log["agent_name"] for log in result["logs"]}, {A_ROOT})
        by_model = {m["model_name"]: m for m in result["by_model"]}
        self.assertEqual((by_model["m1"]["calls"], by_model["m1"]["prompt_tokens"], by_model["m1"]["cost_usd"]),
                         (2, 15, 0.75))
        self.assertEqual((by_model["m2"]["calls"], by_model["m2"]["cost_usd"]), (1, None))
        # Without an agent, every agent's, and no totals
        self.assertNotIn("by_model", self._get_json("/dashboard/api/usage/logs", hours=24))
        response = self.client.get("/dashboard/api/usage/logs", headers=_platform_admin(A),
                                   params={"agent_name": B_ROOT})
        self.assertEqual(response.status_code, 404)

    def test_traces_of_one_agent(self):
        session = self.Session()
        session.execute(text(
            "CREATE TABLE trace_spans (id INTEGER PRIMARY KEY, trace_id TEXT, span_id TEXT, "
            "parent_span_id TEXT, name TEXT, kind TEXT, start_time DATETIME, end_time DATETIME, "
            "duration_ms INTEGER, attributes TEXT, status TEXT, error_message TEXT)"))
        now = datetime.now()
        for trace, agent in (("trace-root", A_ROOT), ("trace-sub", "ws_a_sub")):
            attributes = {"gen_ai.agent.name": agent, "mate.workspace.id": A}
            session.execute(text(
                "INSERT INTO trace_spans (trace_id, span_id, name, start_time, end_time, attributes) "
                "VALUES (:t, 's', 'invoke_agent', :st, :st, :a)"),
                {"t": trace, "st": now, "a": json.dumps(attributes)})
        session.commit()
        session.close()
        traces = lambda **params: sorted(t["trace_id"] for t in self._get_json("/dashboard/api/traces", **params)["traces"])
        self.assertEqual(traces(), ["trace-root", "trace-sub"])
        self.assertEqual(traces(agent_name="ws_a_sub"), ["trace-sub"])
        response = self.client.get("/dashboard/api/traces", headers=_platform_admin(A),
                                   params={"agent_name": B_ROOT})
        self.assertEqual(response.status_code, 404)

    def test_channels_and_test_tabs(self):
        html = self._get(f"/dashboard/agents/{A_ROOT}").text
        for marker in ('id="tab-channels"', 'id="tab-test"', 'id="agentWidgetKeysTable"',
                       'id="widgetKeysModal"', f'data-src="/dashboard/workroom?agent={A_ROOT}&embed=1"'):
            self.assertIn(marker, html)
        # A sub-agent has no app to chat with, nor a widget of its own
        html = self._get("/dashboard/agents/ws_a_sub").text
        self.assertIn('id="tab-channels"', html)
        for marker in ('id="tab-test"', 'id="agentWidgetKeysTable"', 'id="widgetKeysModal"'):
            self.assertNotIn(marker, html)

    def test_embed_shows_the_page_alone(self):
        framed = self._get(f"/dashboard/workroom?agent={A_ROOT}&embed=1").text
        self.assertRegex(framed, r"<body[^>]* data-embed>")
        plain = self._get(f"/dashboard/workroom?agent={A_ROOT}").text
        self.assertNotRegex(plain, r"<body[^>]* data-embed")

    def _get_json(self, path: str, **params):
        response = self.client.get(path, headers=_platform_admin(A), params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_another_workspace_s_agent_is_a_404(self):
        self.assertEqual(self._get(f"/dashboard/agents/{B_ROOT}").status_code, 404)
        self.assertEqual(self._get(f"/dashboard/agents/new?from={B_ROOT}").status_code, 404)
        self.assertEqual(self._get("/dashboard/agents/no_such_agent").status_code, 404)

    def test_new_and_copy(self):
        data = _page_data(self._get(f"/dashboard/agents/new?project_id={self.ids['project_a']}").text)
        self.assertEqual((data["mode"], data["config"], data["defaultProjectId"]),
                         ("new", None, self.ids["project_a"]))
        data = _page_data(self._get(f"/dashboard/agents/new?from={A_ROOT}").text)
        self.assertEqual((data["mode"], data["config"]["name"]), ("copy", A_ROOT))

    def test_the_visual_builder_keeps_its_address(self):
        response = self._get(f"/dashboard/agents/visual?project_id={self.ids['project_a']}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="agent-page-data"', response.text)

    def test_the_parent_picker_offers_the_workspace_s_other_agents(self):
        html = self._get(f"/dashboard/agents/{A_ROOT}").text
        picker = html[html.index('id="agentParentAdd"'):]
        picker = picker[:picker.index("</select>")]
        self.assertIn('value="ws_a_sub"', picker)
        self.assertNotIn(f'value="{A_ROOT}"', picker)
        for marker in B_MARKERS:
            self.assertNotIn(marker, picker)

    def test_a_stored_model_key_is_not_on_the_page(self):
        session = self.Session()
        session.query(AgentConfig).filter_by(name=A_ROOT).update({"model_api_key": "sk-secret-of-a"})
        session.commit()
        session.close()
        response = self._get(f"/dashboard/agents/{A_ROOT}")
        self.assertNotIn("sk-secret-of-a", response.text)
        self.assertEqual(_page_data(response.text)["config"]["model_api_key"], "__stored__")

    def test_an_owner_edits_only_their_workspace_s_agents(self):
        bob = TestClient(auth_server.app, cookies=_signed_in(BOB))
        self.assertEqual(bob.get(f"/dashboard/agents/{B_ROOT}").status_code, 200)
        self.assertEqual(bob.get(f"/dashboard/agents/{A_ROOT}").status_code, 404)

    def test_the_hardcoded_switch_is_for_platform_administrators(self):
        bob = TestClient(auth_server.app, cookies=_signed_in(BOB))
        html = bob.get(f"/dashboard/agents/{B_ROOT}").text
        self.assertRegex(html, r'id="agentHardcoded"\s+disabled')
        html = self._get(f"/dashboard/agents/{A_ROOT}").text
        self.assertNotRegex(html, r'id="agentHardcoded"\s+disabled')


class TestRolesInUse(_TwoWorkspaces):

    def test_the_workspace_s_roles_with_their_members(self):
        session = self.Session()
        try:
            session.query(WorkspaceMember).filter_by(workspace_id=A).update(
                {"agent_roles": json.dumps(["user", "support", "admin"])})
            session.commit()
            members = session.query(WorkspaceMember).filter_by(workspace_id=A).count()
            roles = {r["name"]: r["members"] for r in roles_in_use(session, A)}
        finally:
            session.close()
        # The agents' "widget" and the members' "admin" are platform roles
        self.assertEqual(roles, {"support": members, "user": members})

    def test_a_role_only_an_agent_names_has_no_members(self):
        session = self.Session()
        try:
            session.query(AgentConfig).filter_by(name="ws_a_sub").update(
                {"allowed_for_roles": '["auditor"]'})
            session.commit()
            roles = {r["name"]: r["members"] for r in roles_in_use(session, A)}
            other = {r["name"] for r in roles_in_use(session, B)}
        finally:
            session.close()
        self.assertEqual(roles["auditor"], 0)
        self.assertNotIn("auditor", other)


if __name__ == "__main__":
    unittest.main()
