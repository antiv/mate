#!/usr/bin/env python3
"""
Tests for bearer token expiry.

Tokens from POST /auth/token were kept in an in-memory set with no TTL, so a
leaked token stayed valid until the process restarted or it was revoked.
"""

import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import auth_utils


class TestTokenExpiry(unittest.TestCase):

    def setUp(self):
        auth_utils._active_tokens.clear()

    def test_fresh_token_is_valid(self):
        token = auth_utils.generate_token()
        self.assertTrue(auth_utils.verify_token(token))

    def test_expired_token_is_rejected_and_dropped(self):
        token = auth_utils.generate_token()
        auth_utils._active_tokens[token] = datetime.now() - timedelta(seconds=1)
        self.assertFalse(auth_utils.verify_token(token))
        self.assertNotIn(token, auth_utils._active_tokens)

    def test_unknown_token_is_rejected(self):
        self.assertFalse(auth_utils.verify_token("not-a-token"))

    def test_revoke_still_works(self):
        token = auth_utils.generate_token()
        auth_utils.revoke_token(token)
        self.assertFalse(auth_utils.verify_token(token))

    def test_revoking_unknown_token_does_not_raise(self):
        auth_utils.revoke_token("never-existed")

    def test_ttl_is_configurable(self):
        with patch.dict(os.environ, {"TOKEN_TTL_HOURS": "1"}):
            token = auth_utils.generate_token()
            remaining = auth_utils._active_tokens[token] - datetime.now()
        self.assertLess(remaining, timedelta(hours=1, seconds=5))
        self.assertGreater(remaining, timedelta(minutes=55))

    def test_bad_ttl_value_falls_back_to_default(self):
        with patch.dict(os.environ, {"TOKEN_TTL_HOURS": "not-a-number"}):
            self.assertEqual(auth_utils._token_ttl(), timedelta(hours=24))


if __name__ == "__main__":
    unittest.main()
