"""
A Slack integration's team id is the one Slack's auth.test gives for its bot token,
never the one in the request (issue #372). Slack is an httpx.MockTransport here.
"""

import unittest
from typing import Any, List, Optional
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

import auth_server
from server import slack_routes
from shared.test.test_cross_workspace import (
    A, A_ROOT, ALICE, B, B_ROOT, _platform_admin, _signed_in, _TwoWorkspaces,
)
from shared.utils.models import ChannelIntegration

API = "/dashboard/api/channel-integrations"
TOKEN = "xoxb-of-acme"


class TestSlackTeamId(_TwoWorkspaces):

    def setUp(self):
        super().setUp()
        self.asked: List[httpx.Request] = []
        self.answer: Any = {"ok": True, "team_id": "T_ACME", "team": "Acme & Co"}

        def slack(request: httpx.Request) -> httpx.Response:
            self.asked.append(request)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer if isinstance(self.answer, httpx.Response) else httpx.Response(200, json=self.answer)

        real_client = httpx.AsyncClient

        def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            self.assertIsNotNone(kwargs.get("timeout"))
            return real_client(transport=httpx.MockTransport(slack), **kwargs)

        patcher = patch.object(httpx, "AsyncClient", client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, workspace: int = A, **body: Any) -> httpx.Response:
        agent, project = (A_ROOT, "project_a") if workspace == A else (B_ROOT, "project_b")
        return self.client.post(API, headers=_platform_admin(workspace), json={
            "project_id": self.ids[project], "agent_name": agent, "bot_token": TOKEN,
            "signing_secret": "s", **body})

    def _edit(self, integration_id: int, workspace: int = A, **body: Any) -> httpx.Response:
        return self.client.put(f"{API}/{integration_id}", headers=_platform_admin(workspace), json=body)

    def _add(self, **values: Any) -> int:
        """A row as it was before team ids were checked."""
        session = self.Session()
        row = ChannelIntegration(**{"project_id": self.ids["project_a"], "agent_name": A_ROOT,
                                    "team_id": "T_TYPED", "bot_token": TOKEN, **values})
        session.add(row)
        session.commit()
        row_id = row.id
        session.close()
        return row_id

    def _row(self, integration_id: int) -> ChannelIntegration:
        session = self.Session()
        row = session.get(ChannelIntegration, integration_id)
        session.close()
        return row

    def _in_a(self) -> List[Optional[str]]:
        session = self.Session()
        rows = session.query(ChannelIntegration).filter_by(project_id=self.ids["project_a"]).all()
        session.close()
        return [row.team_id for row in rows]

    def test_the_team_is_the_token_s_whatever_the_request_says(self):
        response = self._create(team_id="T_OTHER", team_name="Other", bot_token=f" {TOKEN}\n")
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()["integration"]
        self.assertEqual((saved["team_id"], saved["team_name"]), ("T_ACME", "Acme & Co"))
        self.assertNotIn(TOKEN, response.text)
        row = self._row(saved["id"])
        self.assertEqual((row.team_id, row.bot_token), ("T_ACME", TOKEN))
        request, = self.asked
        self.assertEqual((request.method, str(request.url)), ("POST", "https://slack.com/api/auth.test"))
        self.assertEqual(parse_qs(request.content.decode()), {"token": [TOKEN]})
        listed = self.client.get(API, headers=_platform_admin(A)).json()["integrations"]
        self.assertEqual([i["team_name"] for i in listed], ["Acme & Co"])

    def test_a_token_slack_refuses_saves_nothing(self):
        for error in ("invalid_auth", "token_revoked"):
            self.answer = {"ok": False, "error": error}
            response = self._create(team_id="T_ACME")
            self.assertEqual((response.status_code, response.json()["detail"]),
                             (400, f"Slack refused the bot token: {error}"))
        self.assertEqual(self._in_a(), [])

    def test_no_answer_from_slack_saves_nothing(self):
        for answer in (httpx.ConnectTimeout("slow"), httpx.ConnectError("down"), httpx.Response(503),
                       httpx.Response(302, headers={"Location": "https://elsewhere.example"}),
                       httpx.Response(429), httpx.Response(200, text="<html>"),
                       httpx.Response(200, json=["?"]), {"ok": True}):
            with self.subTest(answer=answer), self.assertLogs("server.slack_routes", "WARNING") as logs:
                self.answer = answer
                response = self._create(team_id="T_ACME")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertIn("Slack could not be reached", response.json()["detail"])
                self.assertNotIn(TOKEN, "".join(logs.output) + response.text)
        self.assertEqual({r.url.host for r in self.asked}, {"slack.com"})
        self.assertEqual(self._in_a(), [])

    def test_a_token_is_needed(self):
        for token in (None, "", "  "):
            self.assertEqual(self._create(team_id="T_ACME", bot_token=token).status_code, 400)
        self.assertEqual((self.asked, self._in_a()), ([], []))

    def test_a_team_of_another_workspace_s_integration_is_409(self):
        self.assertEqual(self._create(B).status_code, 200)
        response = self._create(A)
        self.assertEqual((response.status_code, response.json()["detail"]), (409, slack_routes._TEAM_TAKEN))
        mine = self._add()
        response = self._edit(mine, bot_token="xoxb-new")
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self._row(mine).team_id, self._row(mine).bot_token), ("T_TYPED", TOKEN))

    def test_a_team_connected_meanwhile_is_409(self):
        self.assertEqual(self._create(B).status_code, 200)
        mine = self._add()
        with patch.object(slack_routes, "_check_team"):
            for response in (self._create(A), self._edit(mine, bot_token="xoxb-new")):
                self.assertEqual((response.status_code, response.json()["detail"]),
                                 (409, slack_routes._TEAM_TAKEN))
        self.assertEqual(self._in_a(), ["T_TYPED"])

    def test_the_project_and_agent_are_checked_before_slack_is_asked(self):
        self.assertEqual(self._create(A, agent_name=B_ROOT).status_code, 404)
        self.assertEqual(self._edit(self._add(), agent_name=B_ROOT, bot_token="x").status_code, 404)
        self.assertEqual(self.asked, [])

    def test_a_new_token_brings_its_team(self):
        mine = self._create().json()["integration"]["id"]
        self.answer = {"ok": True, "team_id": "T_NEW", "team": "New"}
        saved = self._edit(mine, bot_token="xoxb-new", team_id="T_OTHER").json()["integration"]
        self.assertEqual((saved["team_id"], saved["team_name"]), ("T_NEW", "New"))
        for self.answer, status in (({"ok": False, "error": "invalid_auth"}, 400), (httpx.Response(500), 502)):
            response = self._edit(mine, bot_token="xoxb-bad", label="changed")
            self.assertEqual(response.status_code, status, response.text)
            row = self._row(mine)
            self.assertEqual((row.team_id, row.bot_token, row.label), ("T_NEW", "xoxb-new", None))
        self.assertEqual(self._edit(mine, bot_token="").status_code, 400)

    def test_an_edit_without_a_token_of_a_checked_one_asks_nothing(self):
        mine = self._create().json()["integration"]["id"]
        self.asked.clear()
        saved = self._edit(mine, label="Support", team_id="T_OTHER", team_name="Other").json()["integration"]
        self.assertEqual((saved["label"], saved["team_id"], saved["team_name"]),
                         ("Support", "T_ACME", "Acme & Co"))
        self.assertEqual(self.asked, [])

    def test_one_from_before_is_checked_when_it_is_edited(self):
        mine = self._add()
        self.answer = {"ok": False, "error": "token_revoked"}
        self.assertEqual(self._edit(mine, label="x").status_code, 400)
        # It can still be switched off, and its team id stays as it was
        off = self._edit(mine, is_active=False).json()["integration"]
        self.assertEqual((off["is_active"], off["team_id"], off["team_name"]), (False, "T_TYPED", None))
        self.assertEqual(self._edit(mine, is_active=True).status_code, 400)
        self.answer = {"ok": True, "team_id": "T_ACME", "team": "Acme"}
        self.assertEqual(self._edit(mine, is_active=True).json()["integration"]["team_id"], "T_ACME")
        self.assertEqual(parse_qs(self.asked[-1].content.decode()), {"token": [TOKEN]})

    def test_the_platform_is_no_way_around(self):
        other = self._create(platform="discord", team_id="T_OTHER").json()["integration"]
        self.assertEqual((other["team_id"], self.asked), (None, []))
        self.answer = {"ok": False, "error": "invalid_auth"}
        self.assertEqual(self._edit(other["id"], platform="slack", is_active=False).status_code, 400)
        self.assertEqual(self._row(other["id"]).platform, "discord")

    def test_a_member_may_not(self):
        member = TestClient(auth_server.app, cookies=_signed_in(ALICE), follow_redirects=False)
        response = member.post(API, json={"project_id": self.ids["project_a"], "agent_name": A_ROOT,
                                          "bot_token": TOKEN})
        self.assertEqual((response.status_code, self.asked), (403, []))

    def test_the_form_shows_the_team_and_does_not_ask_for_it(self):
        html = self.client.get("/dashboard/integrations", headers=_platform_admin(A)).text
        self.assertIn('<p id="integrationTeam"', html)
        self.assertNotIn("integrationTeamId", html)


if __name__ == "__main__":
    unittest.main()
