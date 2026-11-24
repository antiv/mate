#!/usr/bin/env python3
"""
A first single sign-on: what the new user sees while they wait to be let in, who is told,
and the invitations of their address, which they accept without the link.
"""

import os
import sys
import unittest
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from server import auth, oauth_routes
from server.invitation_routes import utc_now
from shared.test.test_cross_workspace import A, B, _TwoWorkspaces, _platform_admin, auth_server
from shared.utils import notify, signup_notify
from shared.utils.models import User, Workspace, WorkspaceInvitation, WorkspaceMember

WAITING = "waiting for an administrator to let you in"


class _Google:
    def __init__(self, email: str, verified: Any = True):
        self.info = {"email": email, "name": "Nova Osoba"}
        if verified is not None:
            self.info["email_verified"] = verified

    async def authorize_access_token(self, request: Any) -> Dict[str, Any]:
        return {"userinfo": self.info}


class TestNewSignUps(_TwoWorkspaces):

    def setUp(self) -> None:
        super().setUp()
        self.admin = TestClient(auth_server.app)
        patcher = patch.object(signup_notify, "announce")
        self.announced = patcher.start()
        self.addCleanup(patcher.stop)

    def sign_in(self, email: str, verified: Any = True) -> TestClient:
        client = TestClient(auth_server.app)
        with patch.object(oauth_routes, "_get_oauth", return_value=SimpleNamespace(google=_Google(email, verified))):
            self.response = client.get("/auth/callback/google", follow_redirects=False)
        return client

    def row(self, model: Any, **filters: Any) -> Any:
        session = self.Session()
        try:
            return session.query(model).filter_by(**filters).first()
        finally:
            session.close()

    def waiting(self) -> int:
        return self.admin.get("/dashboard/api/users/waiting", headers=_platform_admin(A)).json()["total"]

    def invite(self, email: str, workspace: int = B, role: str = "member", days: int = 7) -> int:
        session = self.Session()
        try:
            invitation = WorkspaceInvitation(
                workspace_id=workspace, email=email, role=role, token_hash=os.urandom(32).hex(),
                created_by="boss@b.example", created_at=utc_now(), expires_at=utc_now() + timedelta(days=days))
            session.add(invitation)
            session.commit()
            return invitation.id
        finally:
            session.close()

    def accept(self, client: TestClient, invitation_id: Any) -> Any:
        return client.post("/dashboard/api/invitations/accept", json={"invitation_id": invitation_id})

    # -- waiting

    def test_a_new_user_is_told_they_wait_and_counted(self) -> None:
        before = self.waiting()
        client = self.sign_in("novi@example.com")
        self.assertIn(WAITING, client.get("/dashboard/workroom").text)
        self.assertEqual(self.waiting(), before + 1)
        self.announced.assert_called_once_with("novi@example.com", "Nova Osoba", "google")
        # Signing in again announces nothing
        self.sign_in("novi@example.com")
        self.announced.assert_called_once()

    def test_once_let_in_they_wait_no_more(self) -> None:
        before = self.waiting()
        client = self.sign_in("novi@example.com")
        session = self.Session()
        session.query(User).filter_by(user_id="novi@example.com").one().roles = "[]"
        session.commit()
        session.close()
        self.assertNotIn(WAITING, client.get("/dashboard/workroom").text)
        self.assertEqual(self.waiting(), before)

    def test_a_role_that_lets_in_announces_nothing(self) -> None:
        with patch.dict(os.environ, {"OAUTH_DEFAULT_ROLE": "user"}):
            self.sign_in("odmah@example.com")
        self.announced.assert_not_called()

    def test_only_a_platform_administrator_gets_the_count(self) -> None:
        self.assertEqual(self.sign_in("novi@example.com").get("/dashboard/api/users/waiting").status_code, 403)

    def test_a_role_that_only_contains_the_word_is_not_waiting(self) -> None:
        before = self.waiting()
        session = self.Session()
        session.add(User(user_id="x@example.com", roles='["suspending"]'))
        session.commit()
        session.close()
        self.assertEqual(self.waiting(), before)

    def test_an_address_google_has_not_verified_is_refused(self) -> None:
        for verified in (False, None, "false"):
            self.sign_in("tudja@example.com", verified)
            self.assertEqual(self.response.headers["location"], "/login?error=email_not_verified")
        self.assertIsNone(self.row(User, user_id="tudja@example.com"))

    # -- invitations

    def test_an_invitation_is_offered_and_accepted_without_the_link(self) -> None:
        invitation = self.invite("Novi@Example.com", role="admin")
        client = self.sign_in("novi@example.com")
        page = client.get("/dashboard/workroom").text
        self.assertIn(f'data-invitation="{invitation}"', page)
        self.assertIn("boss@b.example invited you", page)
        response = self.accept(client, invitation)
        self.assertEqual((response.status_code, response.json()["workspace_id"]), (200, B))
        session = self.Session()
        members = session.query(WorkspaceMember).filter_by(user_id="novi@example.com").all()
        self.assertEqual([(m.workspace_id, m.role, m.invited_by) for m in members], [(B, "admin", "boss@b.example")])
        session.close()
        self.assertNotIn("pending", self.row(User, user_id="novi@example.com").get_roles())
        self.assertIsNotNone(self.row(WorkspaceInvitation, id=invitation).accepted_at)
        self.assertNotIn("data-invitation", client.get("/dashboard/workroom").text)
        self.assertIn(self.accept(client, invitation).status_code, (404, 410))

    def test_anothers_invitation_is_not_found(self) -> None:
        invitation = self.invite("neko@example.com")
        client = self.sign_in("novi@example.com")
        self.assertNotIn("data-invitation", client.get("/dashboard/workroom").text)
        for wanted in (invitation, str(invitation), [invitation], True, None, 10 ** 9):
            self.assertEqual(self.accept(client, wanted).status_code, 404, wanted)
        self.assertIsNone(self.row(WorkspaceInvitation, id=invitation).accepted_at)

    def test_one_that_is_over_is_not_offered(self) -> None:
        expired = self.invite("novi@example.com", days=-1)
        closed = self.invite("novi@example.com")
        session = self.Session()
        session.get(Workspace, B).suspended_at = utc_now()
        session.commit()
        session.close()
        client = self.sign_in("novi@example.com")
        self.assertNotIn("data-invitation", client.get("/dashboard/workroom").text)
        for invitation in (expired, closed):
            self.assertEqual(self.accept(client, invitation).status_code, 404)

    def test_without_a_provider_the_link_is_needed(self) -> None:
        """Nobody verified the address of an account that signs in with a password."""
        session = self.Session()
        admin = session.query(User).filter_by(user_id=auth.AUTH_USERNAME).first() or User(
            user_id=auth.AUTH_USERNAME, roles='["admin"]')
        admin.email = "root@example.com"
        session.add(admin)
        session.commit()
        session.close()
        invitation = self.invite("root@example.com")
        headers = _platform_admin(A)
        self.assertNotIn("data-invitation", self.admin.get("/dashboard/workroom", headers=headers).text)
        response = self.admin.post("/dashboard/api/invitations/accept", headers=headers,
                                   json={"invitation_id": invitation})
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.row(WorkspaceInvitation, id=invitation).accepted_at)


class TestNotification(unittest.TestCase):

    def announce(self, env: Dict[str, str], name: str = "Nova Osoba") -> Optional[List[Any]]:
        with patch.dict(os.environ, {"NEW_USER_NOTIFY_TYPE": "", "NEW_USER_NOTIFY_URL": "", "NEW_USER_NOTIFY_TO": "",
                                     "MATE_PUBLIC_URL": "https://mate.example/", **env}), \
                patch.object(signup_notify.threading, "Thread", lambda target, args, **kw: SimpleNamespace(
                    start=lambda: target(*args))), \
                patch.object(notify, "deliver", return_value=(True, "ok")) as deliver:
            signup_notify.announce("novi@example.com", name, "google")
        return deliver.call_args

    def test_nothing_is_sent_without_a_complete_destination(self) -> None:
        for env in ({}, {"NEW_USER_NOTIFY_TYPE": "slack"}, {"NEW_USER_NOTIFY_TYPE": "nope", "NEW_USER_NOTIFY_URL": "x"},
                    {"NEW_USER_NOTIFY_TYPE": "email", "NEW_USER_NOTIFY_URL": "https://x"}):
            self.assertIsNone(self.announce(env))

    def test_each_kind(self) -> None:
        for kind, field, value in (("slack", "URL", "https://hooks.example/1"), ("email", "TO", "admin@example.com")):
            sent = self.announce({"NEW_USER_NOTIFY_TYPE": kind, f"NEW_USER_NOTIFY_{field}": value})
            self.assertEqual(sent.args, (kind, {"type": kind, field.lower(): value}))
            self.assertIn("Nova Osoba (novi@example.com) signed in with google", sent.kwargs["text"])
            self.assertIn("https://mate.example/dashboard/users?role=pending", sent.kwargs["text"])

    def test_a_name_is_one_short_line(self) -> None:
        sent = self.announce({"NEW_USER_NOTIFY_TYPE": "slack", "NEW_USER_NOTIFY_URL": "https://x"}, "a\nb " + "c" * 500)
        self.assertIn("a b ccc", sent.kwargs["payload"]["message"])
        self.assertLess(len(sent.kwargs["payload"]["message"]), 250)

    def test_a_failure_stays_here(self) -> None:
        with patch.object(notify, "deliver", side_effect=RuntimeError("x")), \
                patch.dict(os.environ, {"NEW_USER_NOTIFY_TYPE": "slack", "NEW_USER_NOTIFY_URL": "https://x"}), \
                self.assertLogs("shared.utils.signup_notify", "WARNING"):
            signup_notify._send({"message": "m", "link": "l", "user_id": "u"})


if __name__ == "__main__":
    unittest.main()
