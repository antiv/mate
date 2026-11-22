"""
The agent page: /dashboard/agents/<name>, /dashboard/agents/new and its Copy
(?from=<name>), on the two workspaces of test_cross_workspace (dashboard-2.1.md,
"The agent page").
"""

import json
import re
import unittest

from shared.test.test_cross_workspace import (
    A, A_ROOT, B, B_MARKERS, B_ROOT, BOB, _platform_admin, _signed_in, _TwoWorkspaces,
)
from shared.utils.models import AgentConfig, WorkspaceMember
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
