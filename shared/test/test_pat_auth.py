#!/usr/bin/env python3
"""
Unit tests for Personal Access Token authentication (server.pat_auth).
"""

import base64
import hashlib
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import asyncio

from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.pat_auth import (
    DEFAULT_ALLOWED_ROLES,
    get_allowed_roles,
    get_pat_user,
    verify_pat_hash,
)


class TestVerifyPatHash(unittest.TestCase):

    def test_hash_matches_sha256(self):
        self.assertEqual(
            verify_pat_hash("mate_secret"),
            hashlib.sha256(b"mate_secret").hexdigest(),
        )

    def test_hash_is_deterministic(self):
        self.assertEqual(verify_pat_hash("abc"), verify_pat_hash("abc"))

    def test_different_tokens_hash_differently(self):
        self.assertNotEqual(verify_pat_hash("abc"), verify_pat_hash("abd"))


class TestGetAllowedRoles(unittest.TestCase):

    def test_default_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_allowed_roles(), DEFAULT_ALLOWED_ROLES)

    def test_env_override_replaces_default(self):
        with patch.dict(os.environ, {"ALLOWED_API_ROLES": "analyst,auditor"}, clear=False):
            self.assertEqual(get_allowed_roles(), {"analyst", "auditor"})

    def test_env_values_are_stripped_and_lowercased(self):
        with patch.dict(os.environ, {"ALLOWED_API_ROLES": " Analyst , AUDITOR "}, clear=False):
            self.assertEqual(get_allowed_roles(), {"analyst", "auditor"})

    def test_empty_entries_are_discarded(self):
        with patch.dict(os.environ, {"ALLOWED_API_ROLES": "analyst,,  ,auditor"}, clear=False):
            self.assertEqual(get_allowed_roles(), {"analyst", "auditor"})

    def test_blank_env_falls_back_to_default(self):
        with patch.dict(os.environ, {"ALLOWED_API_ROLES": ""}, clear=False):
            self.assertEqual(get_allowed_roles(), DEFAULT_ALLOWED_ROLES)


def _request(auth_header: str = "") -> MagicMock:
    """Build a mock Request carrying an Authorization header and empty session."""
    request = MagicMock()
    request.headers = {"Authorization": auth_header} if auth_header else {}
    request.session = {}
    return request


class TestGetPatUser(unittest.TestCase):
    """Covers the authentication boundary: bad tokens must never authenticate."""

    def setUp(self):
        self.session = MagicMock()
        db = MagicMock()
        db.get_session.return_value = self.session
        self._db_patch = patch("server.pat_auth.get_database_client", return_value=db)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)

    def _call(self, request):
        return asyncio.run(get_pat_user(request))

    def _set_token_record(self, record):
        """Point the PersonalAccessToken query at `record`."""
        self.session.query.return_value.filter_by.return_value.first.return_value = record

    def test_missing_credentials_returns_401(self):
        self._set_token_record(None)
        with self.assertRaises(HTTPException) as ctx:
            self._call(_request())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_unknown_bearer_token_returns_401(self):
        self._set_token_record(None)
        with self.assertRaises(HTTPException) as ctx:
            self._call(_request("Bearer not-a-real-token"))
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Invalid", ctx.exception.detail)

    def test_expired_token_returns_401(self):
        record = MagicMock()
        record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
        self._set_token_record(record)
        with self.assertRaises(HTTPException) as ctx:
            self._call(_request("Bearer expired-token"))
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("expired", ctx.exception.detail.lower())

    def test_unexpired_token_authenticates_and_records_use(self):
        user = MagicMock()
        user.user_id = "alice"
        user.get_roles.return_value = ["admin"]
        record = MagicMock()
        record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        record.user = user
        self._set_token_record(record)

        result = self._call(_request("Bearer good-token"))

        self.assertIs(result, user)
        self.assertIsNotNone(record.last_used_at)
        self.session.commit.assert_called()

    def test_token_without_expiry_is_accepted(self):
        user = MagicMock()
        user.user_id = "alice"
        user.get_roles.return_value = ["admin"]
        record = MagicMock()
        record.expires_at = None
        record.user = user
        self._set_token_record(record)

        self.assertIs(self._call(_request("Bearer never-expires")), user)

    def test_valid_token_with_disallowed_role_returns_403(self):
        user = MagicMock()
        user.user_id = "bob"
        user.get_roles.return_value = ["pending"]
        user.has_role.return_value = False
        record = MagicMock()
        record.expires_at = None
        record.user = user
        self._set_token_record(record)

        with patch("server.pat_auth.AUTH_USERNAME", "admin"):
            with self.assertRaises(HTTPException) as ctx:
                self._call(_request("Bearer valid-but-unauthorized"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_database_unavailable_returns_503(self):
        db = MagicMock()
        db.get_session.return_value = None
        with patch("server.pat_auth.get_database_client", return_value=db):
            with self.assertRaises(HTTPException) as ctx:
                self._call(_request("Bearer anything"))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_basic_auth_with_wrong_password_returns_401(self):
        self._set_token_record(None)
        header = "Basic " + base64.b64encode(b"admin:wrong-password").decode()
        with patch("server.pat_auth.AUTH_USERNAME", "admin"), \
             patch("server.pat_auth.AUTH_PASSWORD", "right-password"), \
             patch("server.pat_auth.is_basic_auth_logged_out", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                self._call(_request(header))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_session_is_closed_even_when_auth_fails(self):
        self._set_token_record(None)
        with self.assertRaises(HTTPException):
            self._call(_request("Bearer bad"))
        self.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
