"""
Search in the Work Room (documents/design/dashboard-2.1.md, "Search in the Work Room"):
the agents of the welcome page, filtered in the browser, and the user's sessions with
an agent, whose messages GET /dashboard/api/workroom/sessions/search searches.
"""

import json
import unittest
from datetime import datetime, timezone

from server import auth
from shared.test.test_cross_workspace import A, A_ROOT, B_ROOT, _platform_admin, _TwoWorkspaces
from shared.utils.models import LangGraphEvent, LangGraphSession

SEARCH = "/dashboard/api/workroom/sessions/search"


class TestWorkRoomSearch(_TwoWorkspaces):

    def _add_session(self, session_id: str, user_id: str, texts, app_name: str = A_ROOT, at: float = 0.0):
        session = self.Session()
        session.add(LangGraphSession(id=session_id, app_name=app_name, user_id=user_id))
        for n, text in enumerate(texts):
            session.add(LangGraphEvent(id=f"{session_id}-{n}", session_id=session_id, author="user",
                                       content=json.dumps({"role": "user", "parts": [{"text": text}]}),
                                       timestamp=(at or datetime.now(timezone.utc).timestamp()) + n))
        session.commit()
        session.close()

    def _search(self, q: str, agent: str = A_ROOT):
        return self.client.get(SEARCH, headers=_platform_admin(A), params={"agent": agent, "q": q})

    def test_the_messages_of_the_caller_s_sessions(self):
        me = auth.AUTH_USERNAME
        self._add_session("mine-1", me, ["Kako da čuvam ključeve?", "A gde je backup?"], at=1000.0)
        self._add_session("mine-2", me, ["Nothing about that here"], at=2000.0)
        self._add_session("theirs", "someone-else", ["Kako da čuvam ključeve?"])
        self._add_session("other-app", me, ["Kako da čuvam ključeve?"], app_name="ws_a_sub")

        response = self._search("ČUVAM ključeve")
        self.assertEqual(response.status_code, 200, response.text)
        sessions = response.json()["sessions"]
        # Every word, in any case, with the letters JSON stores escaped; only the
        # caller's sessions, and only with the agent named
        self.assertEqual([s["id"] for s in sessions], ["mine-1"])
        self.assertIn("čuvam ključeve", sessions[0]["snippet"])
        self.assertTrue(sessions[0]["updated_at"].startswith("1970-01-01T00:16"))

        self.assertEqual([s["id"] for s in self._search("backup gde").json()["sessions"]], ["mine-1"])
        self.assertEqual(self._search("nowhere to be found").json()["sessions"], [])

    def test_another_workspace_s_agent_is_a_404(self):
        self.assertEqual(self._search("x", agent=B_ROOT).status_code, 404)

    def test_the_query_is_bounded(self):
        self.assertEqual(self._search("").status_code, 422)
        self.assertEqual(self._search("x" * 201).status_code, 422)

    def test_the_pages_have_their_search_boxes(self):
        welcome = self.client.get("/dashboard/workroom", headers=_platform_admin(A)).text
        self.assertIn('id="wrAgentSearch"', welcome)
        self.assertRegex(welcome, rf'class="wr-agent-card"\s+data-search="[^"]*{A_ROOT}')
        chat = self.client.get(f"/dashboard/workroom?agent={A_ROOT}", headers=_platform_admin(A)).text
        self.assertIn('id="workroomSessionSearch"', chat)


if __name__ == "__main__":
    unittest.main()
