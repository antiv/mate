"""
The command palette (documents/design/dashboard-2.1.md, "command palette"): the pages
it offers are those of the sidebars, and GET /dashboard/api/palette gives each caller
the agents they may open and their own conversations.
"""

import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import auth_server
from server import auth
from shared.test.test_cross_workspace import (
    A, A_ROOT, ALICE, B_MARKERS, B_ROOT, BOB, _platform_admin, _signed_in, _TwoWorkspaces,
)
from shared.utils.models import AgentConfig, LangGraphSession

PALETTE = "/dashboard/api/palette"


def _pages(html: str) -> dict:
    found = re.search(r'<script type="application/json" id="command-palette-pages">(.*?)</script>', html, re.S)
    return {p["url"]: p for p in json.loads(found.group(1))} if found else {}


class TestPalettePages(unittest.TestCase):

    SPACES = ("/dashboard/overview", "/dashboard/agents", "/dashboard/workroom")

    def _render(self, path: str, is_admin=True, platform_admin=True, wizard=False, shop=False, query=b"", username="u"):
        templates = Jinja2Templates(directory=str(Path(__file__).parent.parent.parent / "templates"))
        templates.env.globals["is_platform_admin"] = lambda request: platform_admin
        templates.env.globals["features_in_use"] = lambda: {"wizard": wizard, "shop": shop}
        request = Request({"type": "http", "http_version": "1.1", "method": "GET", "path": path,
                           "headers": [], "query_string": query, "session": {}})
        request.state.csp_nonce = "n"
        return templates.TemplateResponse(request, "base.html", {
            "request": request, "username": username, "is_admin": is_admin}).body.decode()

    def test_the_pages_are_those_of_the_sidebars(self):
        for who in ({}, {"wizard": True, "shop": True}, {"platform_admin": False, "wizard": True, "shop": True},
                    {"is_admin": False, "platform_admin": False}):
            with self.subTest(who=who):
                in_sidebars = {}
                for space in self.SPACES:
                    html = self._render(space, **who)
                    aside = html[html.index('<aside id="sidebar"'):html.index("</aside>")]
                    in_sidebars.update((url, title) for url, title in re.findall(
                        r'<a href="(/dashboard/[^"?]*)"[^>]*title="([^"]*)"', aside))
                # The Work Room's sidebar calls its own link New Conversation
                in_sidebars.pop("/dashboard/workroom", None)
                for link, title in re.findall(r'<a id="newConversationLink"\s+href="([^"?]*)"[^>]*title="([^"]*)"', html):
                    self.assertEqual(link, "/dashboard/workroom")
                pages = _pages(html)
                self.assertIn("/dashboard/workroom", pages)
                names = {url: p["label"] for url, p in pages.items() if url != "/dashboard/workroom"}
                self.assertEqual(names, {url: title.replace("&amp;", "&") for url, title in in_sidebars.items()})

    def test_it_is_for_whoever_is_signed_in_and_not_in_a_frame(self):
        html = self._render("/dashboard/overview")
        for marker in ('id="commandPalette"', 'data-click="mateCommandPalette.open"', "/static/js/command-palette.js"):
            self.assertIn(marker, html)
        for other in (self._render("/dashboard/workroom", query=b"embed=1"), self._render("/login", username=None)):
            self.assertNotIn("commandPalette", other)
            self.assertNotIn("command-palette", other.replace("Command palette", ""))


class TestPaletteRoute(_TwoWorkspaces):

    def _add(self, session_id, user_id, app_name=A_ROOT, title=None, at=1000):
        session = self.Session()
        session.add(LangGraphSession(id=session_id, app_name=app_name, user_id=user_id,
                                     state=json.dumps({"_title": title} if title else {}),
                                     updated_at=datetime.fromtimestamp(at, tz=timezone.utc)))
        session.commit()
        session.close()

    def _member(self, user_id):
        return TestClient(auth_server.app, cookies=_signed_in(user_id), follow_redirects=False)

    def test_an_admin_goes_to_the_agents_pages(self):
        response = self.client.get(PALETTE, headers=_platform_admin(A))
        self.assertEqual(response.status_code, 200, response.text)
        agents = {a["name"]: a["url"] for a in response.json()["agents"]}
        self.assertEqual(agents, {A_ROOT: f"/dashboard/agents/{A_ROOT}", "ws_a_sub": "/dashboard/agents/ws_a_sub"})
        for marker in B_MARKERS:
            self.assertNotIn(marker, response.text)
        self.assertEqual(set(response.json()["agents"][0]), {"name", "description", "project", "url"})

    def test_a_member_goes_to_the_agents_they_may_chat_with(self):
        agents = self._member(ALICE).get(PALETTE).json()["agents"]
        self.assertEqual([(a["name"], a["url"]) for a in agents], [(A_ROOT, f"/dashboard/workroom?agent={A_ROOT}")])
        session = self.Session()
        session.query(AgentConfig).filter_by(name=A_ROOT).update({"allowed_for_roles": '["auditor"]'})
        session.commit()
        session.close()
        self._add("alices", ALICE)
        answer = self._member(ALICE).get(PALETTE).json()
        # Nor to a conversation with one they no longer may
        self.assertEqual((answer["agents"], answer["sessions"]), ([], []))

    def test_an_owner_of_another_workspace_gets_theirs(self):
        answer = self._member(BOB).get(PALETTE)
        self.assertEqual(answer.status_code, 200)
        self.assertIn(B_ROOT, answer.text)
        self.assertNotIn(A_ROOT, answer.text)

    def test_the_caller_s_own_sessions_newest_first(self):
        self._add("old", ALICE, at=1000)
        self._add("new", ALICE, title="Keys & šifre", at=2000)
        self._add("admins", auth.AUTH_USERNAME, at=3000)
        self._add("in-b", ALICE, app_name=B_ROOT, at=4000)
        self._add("no-config", ALICE, app_name="gone", at=5000)
        sessions = self._member(ALICE).get(PALETTE).json()["sessions"]
        self.assertEqual([(s["title"], s["agent"]) for s in sessions], [("Keys & šifre", A_ROOT), ("", A_ROOT)])
        self.assertEqual(sessions[0]["url"], f"/dashboard/workroom?agent={A_ROOT}&session=new")
        self.assertTrue(sessions[0]["updated_at"].startswith("1970-01-01T00:33:20"))

    def test_an_id_is_escaped_in_its_address(self):
        self._add("a&agent=x/../y", ALICE)
        url = self._member(ALICE).get(PALETTE).json()["sessions"][0]["url"]
        self.assertEqual(url, f"/dashboard/workroom?agent={A_ROOT}&session=a%26agent%3Dx%2F..%2Fy")

    def test_at_most_twenty_sessions(self):
        for i in range(25):
            self._add(f"s{i}", ALICE, at=1000 + i)
        sessions = self._member(ALICE).get(PALETTE).json()["sessions"]
        self.assertEqual((len(sessions), sessions[0]["url"].rsplit("=", 1)[1]), (20, "s24"))

    def test_nobody_signed_in_is_refused(self):
        self.assertNotEqual(TestClient(auth_server.app, follow_redirects=False).get(PALETTE).status_code, 200)


if __name__ == "__main__":
    unittest.main()
