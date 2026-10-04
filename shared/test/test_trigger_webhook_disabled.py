#!/usr/bin/env python3
"""
A disabled trigger does not run from its webhook (#150).

`is_enabled` used to be checked only on the cron path, so switching a webhook
trigger off in the dashboard did not stop `POST /triggers/{id}/fire`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.test.test_trigger_signature import FIRE_KEY, FireEndpointTestCase, sign


class DisabledTriggerTestCase(FireEndpointTestCase):

    require_signature = False
    is_enabled = False

    def setUp(self):
        super().setUp()
        session = self.server.db_client.get_session.return_value
        session.query.return_value.filter.return_value.first.return_value.is_enabled = self.is_enabled


class TestDisabledTrigger(DisabledTriggerTestCase):

    def test_fire_key_is_refused_with_409(self):
        resp = self.fire(b'{"key":"MT-32"}')
        self.assertEqual(resp.status_code, 409)
        self.assertIn("disabled", resp.json()["detail"])
        self.runner.execute_trigger.assert_not_called()

    def test_dashboard_auth_is_refused_with_409(self):
        resp = self.fire(b'{"key":"MT-32"}', key=None)
        self.assertEqual(resp.status_code, 409)
        self.runner.execute_trigger.assert_not_called()

    def test_a_wrong_key_still_gets_403_not_409(self):
        # Authentication comes first: an unauthenticated caller learns nothing
        # about whether the trigger is switched on.
        resp = self.fire(b'{"key":"MT-32"}', key="wrong-key")
        self.assertEqual(resp.status_code, 403)
        self.runner.execute_trigger.assert_not_called()


class TestDisabledSignedTrigger(DisabledTriggerTestCase):

    require_signature = True

    def test_a_bad_signature_still_gets_401_not_409(self):
        resp = self.fire(b'{"key":"MT-32"}', {"X-MATE-Signature": "garbage"})
        self.assertEqual(resp.status_code, 401)

    def test_a_valid_signature_is_refused_with_409(self):
        body = b'{"key":"MT-32"}'
        resp = self.fire(body, {"X-MATE-Signature": sign(body)}, key=FIRE_KEY)
        self.assertEqual(resp.status_code, 409)
        self.runner.execute_trigger.assert_not_called()


class TestEnabledTrigger(DisabledTriggerTestCase):

    is_enabled = True

    def test_an_enabled_trigger_still_fires(self):
        resp = self.fire(b'{"key":"MT-32"}')
        self.assertEqual(resp.status_code, 200)
        self.runner.execute_trigger.assert_called_once_with(7, {"key": "MT-32"})


if __name__ == "__main__":
    unittest.main()
