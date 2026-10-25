#!/usr/bin/env python3
"""
Slack users are MATE users per Slack workspace (1.6.0 item 6).

The id was slack_<user>, so the same Slack user id in two workspaces was one MATE
user: one set of roles, one profile, one store of OAuth credentials and one memory.
It is now slack_<team>_<user>. On the first message under the new id, the roles an
admin gave the old id are copied to it, so no one loses access to a restricted
agent at the upgrade; nothing else of the old id is carried over, and its
sessions and logs stay where they are.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server import slack_routes
from shared.utils.models import Base, User


class _Slack(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        db = MagicMock()
        db.get_session.side_effect = lambda: self.Session()
        self.run_agent = AsyncMock(return_value="hi")
        patch.object(slack_routes, "get_database_client", return_value=db).start()
        patch.object(slack_routes, "run_agent_message", self.run_agent).start()
        patch.object(slack_routes, "_post_reply", AsyncMock()).start()
        self.audit = patch("shared.utils.audit_service.log").start()
        self.addCleanup(patch.stopall)

    def _message(self, team, user="U1", channel="D1", text="hello"):
        integration = {"agent_name": "bot", "bot_token": "xoxb", "team_id": team}
        event = {"channel": channel, "channel_type": "im", "user": user, "text": text, "ts": "1.0"}
        asyncio.run(slack_routes._handle_event(integration, event))
        return self.run_agent.call_args.kwargs["user_id"]

    def _add_user(self, user_id, roles):
        session = self.Session()
        user = User(user_id=user_id)
        user.set_roles(roles)
        session.add(user)
        session.commit()
        session.close()

    def _roles(self, user_id):
        session = self.Session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            return user.get_roles() if user else None
        finally:
            session.close()


class TestUserIdPerWorkspace(_Slack):

    def test_the_user_id_names_the_workspace(self):
        self.assertEqual(self._message("T1"), "slack_T1_U1")

    def test_one_slack_user_id_in_two_workspaces_is_two_users(self):
        self.assertEqual(self._message("T1"), "slack_T1_U1")
        self.assertEqual(self._message("T2"), "slack_T2_U1")

    def test_a_button_click_runs_as_the_same_user(self):
        integration = {"agent_name": "bot", "bot_token": "xoxb", "team_id": "T1"}
        asyncio.run(slack_routes._dispatch(integration, "D1", "U1", "slack_T1_D1", None, "yes"))
        self.assertEqual(self.run_agent.call_args.kwargs["user_id"], "slack_T1_U1")

    def test_sessions_keep_their_ids(self):
        self._message("T1", channel="D9")
        self.assertEqual(self.run_agent.call_args.kwargs["session_id"], "slack_T1_D9")


class TestRolesCarryOver(_Slack):

    def test_the_old_ids_roles_are_copied_on_the_first_message(self):
        self._add_user("slack_U1", ["user", "support"])
        self._message("T1")
        self.assertEqual(self._roles("slack_T1_U1"), ["user", "support"])
        self.assertEqual(self._roles("slack_U1"), ["user", "support"])
        self.audit.assert_called_once()
        self.assertEqual(self.audit.call_args.kwargs["details"],
                         {"roles": ["user", "support"], "roles_from": "slack_U1"})

    def test_each_workspace_gets_them_once(self):
        self._add_user("slack_U1", ["user", "support"])
        self._message("T1")
        self._message("T2")
        self.assertEqual(self._roles("slack_T2_U1"), ["user", "support"])
        self.assertEqual(self.audit.call_count, 2)

    def test_roles_changed_since_are_not_overwritten(self):
        self._add_user("slack_U1", ["user", "support"])
        self._message("T1")
        session = self.Session()
        session.query(User).filter(User.user_id == "slack_T1_U1").one().set_roles(["user"])
        session.commit()
        session.close()
        self._message("T1")
        self.assertEqual(self._roles("slack_T1_U1"), ["user"])
        self.audit.assert_called_once()

    def test_without_an_old_id_nothing_is_created_here(self):
        # The role check creates the user with the default role, as for any new id
        self._message("T1")
        self.assertIsNone(self._roles("slack_T1_U1"))
        self.audit.assert_not_called()

    def test_a_database_error_still_answers(self):
        self._add_user("slack_U1", ["support"])
        with patch.object(User, "set_roles", side_effect=RuntimeError("db")), \
                self.assertLogs("server.slack_routes", "WARNING"):
            self.assertEqual(self._message("T1"), "slack_T1_U1")
        self.audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
