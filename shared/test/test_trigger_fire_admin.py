#!/usr/bin/env python3
"""
Without its fire key, a trigger only fires for an admin.

POST /triggers/{id}/fire falls back to dashboard credentials when no
X-MATE-Trigger-Key is sent. That fallback accepted any signed-in user, while every
other trigger action is admin-only: the route sits outside /dashboard/api, where
the admin middleware applies.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.test.test_trigger_signature import FireEndpointTestCase


class TestFireWithoutKey(FireEndpointTestCase):

    require_signature = False

    def test_admin_fires_without_key(self):
        with patch("server.auth.is_admin_user", return_value=True):
            resp = self.fire(key=None)
        self.assertEqual(resp.status_code, 200)
        self.runner.execute_trigger.assert_called_once()

    def test_signed_in_non_admin_is_refused(self):
        with patch("server.auth.is_admin_user", return_value=False):
            resp = self.fire(key=None)
        self.assertEqual(resp.status_code, 403)
        self.runner.execute_trigger.assert_not_called()

    def test_fire_key_needs_no_admin(self):
        # External callers authenticate with the key, not as a dashboard user
        with patch("server.auth.is_admin_user", return_value=False):
            resp = self.fire()
        self.assertEqual(resp.status_code, 200)
        self.runner.execute_trigger.assert_called_once()


if __name__ == "__main__":
    unittest.main()
