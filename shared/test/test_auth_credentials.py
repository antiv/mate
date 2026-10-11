#!/usr/bin/env python3
"""
The built-in administrator's credentials.

.env.example lists AUTH_USERNAME and AUTH_PASSWORD empty, and os.getenv returns
"" for them rather than its default, so a copied .env used to configure an empty
username and password: the login form and Basic auth then let anyone in with
empty fields, and production's check for the default password did not see it.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import settings
import server.auth as auth


class TestCredentialSettings(unittest.TestCase):

    def test_empty_values_count_as_unset(self):
        with patch.dict(os.environ, {"AUTH_USERNAME": "", "AUTH_PASSWORD": ""}):
            self.assertEqual(settings.auth_username(), "admin")
            # The default, which production refuses at startup
            self.assertEqual(settings.auth_password(), settings.DEFAULT_AUTH_PASSWORD)

    def test_set_values_are_used(self):
        with patch.dict(os.environ, {"AUTH_USERNAME": "boss", "AUTH_PASSWORD": "s3cret"}):
            self.assertEqual((settings.auth_username(), settings.auth_password()), ("boss", "s3cret"))


class TestCredentialsMatch(unittest.TestCase):

    def setUp(self):
        saved = (auth.AUTH_USERNAME, auth.AUTH_PASSWORD)
        self.addCleanup(auth.configure_auth, *saved)

    def test_the_configured_credentials_match(self):
        auth.configure_auth("admin", "s3cret")
        self.assertTrue(auth.credentials_match("admin", "s3cret"))
        self.assertFalse(auth.credentials_match("admin", "wrong"))
        self.assertFalse(auth.credentials_match("other", "s3cret"))

    def test_empty_fields_never_log_in(self):
        auth.configure_auth("admin", "s3cret")
        self.assertFalse(auth.credentials_match("admin", ""))
        self.assertFalse(auth.credentials_match("", ""))
        self.assertFalse(auth.credentials_match(None, None))

    def test_an_empty_configured_password_lets_nobody_in(self):
        auth.configure_auth("", "")
        self.assertFalse(auth.credentials_match("", ""))
        self.assertFalse(auth.credentials_match("", "anything"))


if __name__ == "__main__":
    unittest.main()
