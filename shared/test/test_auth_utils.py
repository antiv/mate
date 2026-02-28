#!/usr/bin/env python3
"""
Unit tests for shared authentication utilities (auth_utils).
"""

import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.auth_utils import (
    generate_token, verify_token, revoke_token, active_tokens,
    logout_basic_auth, is_basic_auth_logged_out, clear_logged_out_status,
    _hash_credentials, _cleanup_logged_out_credentials,
    _active_tokens, _logged_out_credentials, LOGOUT_EXPIRY_MINUTES,
)


class TestTokenGeneration(unittest.TestCase):

    def setUp(self):
        _active_tokens.clear()
        _logged_out_credentials.clear()

    def tearDown(self):
        _active_tokens.clear()
        _logged_out_credentials.clear()

    def test_generate_token_returns_string(self):
        token = generate_token()
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 0)

    def test_generate_token_is_unique(self):
        tokens = {generate_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50)

    def test_generated_token_is_active(self):
        token = generate_token()
        self.assertIn(token, _active_tokens)

    def test_generate_multiple_tokens(self):
        t1 = generate_token()
        t2 = generate_token()
        self.assertIn(t1, _active_tokens)
        self.assertIn(t2, _active_tokens)
        self.assertEqual(len(_active_tokens), 2)


class TestTokenVerification(unittest.TestCase):

    def setUp(self):
        _active_tokens.clear()

    def tearDown(self):
        _active_tokens.clear()

    def test_verify_valid_token(self):
        token = generate_token()
        self.assertTrue(verify_token(token))

    def test_verify_invalid_token(self):
        self.assertFalse(verify_token("invalid_token_xyz"))

    def test_verify_empty_token(self):
        self.assertFalse(verify_token(""))

    def test_verify_after_revoke(self):
        token = generate_token()
        self.assertTrue(verify_token(token))
        revoke_token(token)
        self.assertFalse(verify_token(token))


class TestTokenRevocation(unittest.TestCase):

    def setUp(self):
        _active_tokens.clear()

    def tearDown(self):
        _active_tokens.clear()

    def test_revoke_existing_token(self):
        token = generate_token()
        revoke_token(token)
        self.assertNotIn(token, _active_tokens)

    def test_revoke_nonexistent_token(self):
        revoke_token("nonexistent_token")

    def test_revoke_does_not_affect_others(self):
        t1 = generate_token()
        t2 = generate_token()
        revoke_token(t1)
        self.assertNotIn(t1, _active_tokens)
        self.assertIn(t2, _active_tokens)


class TestBasicAuthLogout(unittest.TestCase):

    def setUp(self):
        _logged_out_credentials.clear()

    def tearDown(self):
        _logged_out_credentials.clear()

    def test_logout_marks_credentials(self):
        logout_basic_auth("admin", "pass")
        self.assertTrue(is_basic_auth_logged_out("admin", "pass"))

    def test_not_logged_out_by_default(self):
        self.assertFalse(is_basic_auth_logged_out("admin", "pass"))

    def test_different_credentials_not_affected(self):
        logout_basic_auth("admin", "pass")
        self.assertFalse(is_basic_auth_logged_out("admin", "different"))
        self.assertFalse(is_basic_auth_logged_out("other", "pass"))

    def test_clear_logged_out_status(self):
        logout_basic_auth("admin", "pass")
        self.assertTrue(is_basic_auth_logged_out("admin", "pass"))
        clear_logged_out_status("admin", "pass")
        self.assertFalse(is_basic_auth_logged_out("admin", "pass"))

    def test_clear_nonexistent_credentials(self):
        clear_logged_out_status("nobody", "nothing")

    def test_logout_expiry(self):
        logout_basic_auth("admin", "pass")
        cred_hash = _hash_credentials("admin", "pass")
        _logged_out_credentials[cred_hash] = datetime.now() - timedelta(minutes=LOGOUT_EXPIRY_MINUTES + 1)
        self.assertFalse(is_basic_auth_logged_out("admin", "pass"))


class TestCredentialHashing(unittest.TestCase):

    def test_hash_is_deterministic(self):
        h1 = _hash_credentials("user", "pass")
        h2 = _hash_credentials("user", "pass")
        self.assertEqual(h1, h2)

    def test_different_credentials_different_hash(self):
        h1 = _hash_credentials("user1", "pass1")
        h2 = _hash_credentials("user2", "pass2")
        self.assertNotEqual(h1, h2)

    def test_hash_is_string(self):
        h = _hash_credentials("u", "p")
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)  # SHA-256 hex digest


class TestCleanupLoggedOutCredentials(unittest.TestCase):

    def setUp(self):
        _logged_out_credentials.clear()

    def tearDown(self):
        _logged_out_credentials.clear()

    def test_cleanup_removes_expired(self):
        old_hash = _hash_credentials("old", "user")
        _logged_out_credentials[old_hash] = datetime.now() - timedelta(minutes=LOGOUT_EXPIRY_MINUTES + 5)
        _cleanup_logged_out_credentials()
        self.assertNotIn(old_hash, _logged_out_credentials)

    def test_cleanup_keeps_recent(self):
        recent_hash = _hash_credentials("recent", "user")
        _logged_out_credentials[recent_hash] = datetime.now()
        _cleanup_logged_out_credentials()
        self.assertIn(recent_hash, _logged_out_credentials)


if __name__ == '__main__':
    unittest.main()
