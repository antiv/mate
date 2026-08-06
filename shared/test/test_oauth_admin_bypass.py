#!/usr/bin/env python3
"""
Tests for the OAuth admin-decision fix.

A session used to be treated as admin when the provider *display name* matched
AUTH_USERNAME (default "admin"). Display names are chosen freely by the account
holder, so renaming a GitHub profile to "admin" granted platform admin.
Only provider-verified identities (user_id / email) or the DB role may do that.
"""

import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server import auth as auth_module
from server.auth import is_admin_user, get_user_role
from server.oauth_routes import _safe_next, _signup_allowed


class _Req:
    """Minimal stand-in for a Starlette Request with a session."""

    def __init__(self, session):
        self.session = session


def _oauth_session(**kwargs):
    base = {"provider": "github", "user_id": "attacker@example.com",
            "email": "attacker@example.com", "display_name": "Attacker"}
    base.update(kwargs)
    return _Req({"user": base})


class TestOAuthAdminDecision(unittest.TestCase):

    def setUp(self):
        self._orig = auth_module.AUTH_USERNAME
        auth_module.AUTH_USERNAME = "admin"
        self._no_db_role = patch("server.auth._oauth_user_has_admin_role", return_value=False)
        self._no_db_role.start()

    def tearDown(self):
        auth_module.AUTH_USERNAME = self._orig
        self._no_db_role.stop()

    def test_display_name_does_not_grant_admin(self):
        req = _oauth_session(display_name="admin")
        self.assertFalse(is_admin_user(req))
        self.assertEqual(get_user_role(req), "user")

    def test_regular_oauth_user_is_not_admin(self):
        self.assertFalse(is_admin_user(_oauth_session()))

    def test_verified_user_id_still_grants_admin(self):
        self.assertTrue(is_admin_user(_oauth_session(user_id="admin")))

    def test_verified_email_still_grants_admin(self):
        self.assertTrue(is_admin_user(_oauth_session(email="admin", user_id="")))

    def test_db_admin_role_grants_admin(self):
        with patch("server.auth._oauth_user_has_admin_role", return_value=True):
            self.assertTrue(is_admin_user(_oauth_session()))

    def test_basic_auth_session_is_admin(self):
        req = _Req({"user": {"provider": "basic", "user_id": "admin", "display_name": "admin"}})
        self.assertTrue(is_admin_user(req))

    def test_no_session_is_admin(self):
        # Basic-auth / bearer callers have no OAuth session and stay admin.
        self.assertTrue(is_admin_user(_Req({})))


class TestSignupAllowlist(unittest.TestCase):

    def test_open_when_unset(self):
        with patch.dict(os.environ, {"OAUTH_ALLOWED_DOMAINS": "", "OAUTH_ALLOWED_EMAILS": ""}):
            self.assertTrue(_signup_allowed("anyone@example.com"))

    def test_domain_allowlist(self):
        with patch.dict(os.environ, {"OAUTH_ALLOWED_DOMAINS": "corp.com", "OAUTH_ALLOWED_EMAILS": ""}):
            self.assertTrue(_signup_allowed("dev@corp.com"))
            self.assertFalse(_signup_allowed("dev@evil.com"))
            self.assertFalse(_signup_allowed("dev@notcorp.com"))

    def test_email_allowlist(self):
        with patch.dict(os.environ, {"OAUTH_ALLOWED_DOMAINS": "", "OAUTH_ALLOWED_EMAILS": "boss@corp.com"}):
            self.assertTrue(_signup_allowed("boss@corp.com"))
            self.assertFalse(_signup_allowed("intern@corp.com"))


class TestSafeNext(unittest.TestCase):

    def test_relative_path_kept(self):
        self.assertEqual(_safe_next("/dashboard/agents"), "/dashboard/agents")

    def test_absolute_url_rejected(self):
        self.assertEqual(_safe_next("https://evil.com"), "/dashboard")

    def test_protocol_relative_rejected(self):
        self.assertEqual(_safe_next("//evil.com"), "/dashboard")

    def test_empty_falls_back(self):
        self.assertEqual(_safe_next(""), "/dashboard")


if __name__ == "__main__":
    unittest.main()
