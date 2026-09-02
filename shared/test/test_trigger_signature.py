#!/usr/bin/env python3
"""
Tests for optional HMAC verification on inbound trigger webhooks (#83).

`POST /triggers/{id}/fire` authenticates with a shared fire key. Since #75 the
body is interpolated into the agent's prompt through `{{ payload }}`, so whoever
holds the key controls text that goes straight into an LLM — and a bearer secret
cannot prove who composed a body. These tests pin the signature check: that it
runs over the raw bytes before parsing, that a valid fire key alone is refused
once verification is required, and that triggers without it behave exactly as
they did before.
"""

import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import AgentTrigger
from shared.utils.trigger_runner import TriggerRunner

SECRET = "whsec_test_secret"
FIRE_KEY = "raw-fire-key"
FIRE_KEY_HASH = hashlib.sha256(FIRE_KEY.encode()).hexdigest()


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature(unittest.TestCase):
    """Unit-level checks on the helper itself."""

    def test_bare_hex_digest_is_accepted(self):
        body = b'{"a":1}'
        self.assertTrue(TriggerRunner.verify_signature(SECRET, body, sign(body)))

    def test_github_style_prefix_is_accepted(self):
        body = b'{"a":1}'
        self.assertTrue(
            TriggerRunner.verify_signature(SECRET, body, "sha256=" + sign(body)))

    def test_uppercase_digest_is_accepted(self):
        body = b'{"a":1}'
        self.assertTrue(
            TriggerRunner.verify_signature(SECRET, body, sign(body).upper()))

    def test_a_different_body_does_not_verify(self):
        self.assertFalse(
            TriggerRunner.verify_signature(SECRET, b'{"a":2}', sign(b'{"a":1}')))

    def test_a_different_secret_does_not_verify(self):
        body = b'{"a":1}'
        self.assertFalse(
            TriggerRunner.verify_signature(SECRET, body, sign(body, "other")))

    def test_missing_secret_or_signature_does_not_verify(self):
        self.assertFalse(TriggerRunner.verify_signature("", b"x", sign(b"x")))
        self.assertFalse(TriggerRunner.verify_signature(SECRET, b"x", ""))
        self.assertFalse(TriggerRunner.verify_signature(SECRET, b"x", None))

    def test_empty_body_still_signs(self):
        # A webhook that fires with nothing is normal; it must still be signable.
        self.assertTrue(TriggerRunner.verify_signature(SECRET, b"", sign(b"")))

    def test_comparison_is_constant_time(self):
        body = b'{"a":1}'
        with patch("shared.utils.trigger_runner.hmac.compare_digest",
                   wraps=hmac.compare_digest) as cmp:
            TriggerRunner.verify_signature(SECRET, body, sign(body))
        cmp.assert_called_once()

    def test_generated_secrets_are_unique_and_prefixed(self):
        a, b = TriggerRunner.generate_signing_secret(), TriggerRunner.generate_signing_secret()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("whsec_"))


class FireEndpointTestCase(unittest.TestCase):
    """Drives the real /triggers/{id}/fire route against a stubbed trigger row."""

    require_signature = True

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.utils.dashboard.dashboard_server import DashboardServer

        with patch("shared.utils.database_client.get_database_client",
                   return_value=MagicMock()):
            project_root = Path(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
            app = FastAPI()
            self.server = DashboardServer(app, project_root)

        trigger_row = MagicMock(
            id=7,
            fire_key_hash=FIRE_KEY_HASH,
            signing_secret=SECRET,
            require_signature=self.require_signature,
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = trigger_row
        self.server.db_client = MagicMock()
        self.server.db_client.get_session.return_value = session

        self.runner = MagicMock()
        self.runner.execute_trigger.return_value = {"status": "ok", "agent_response": "x"}
        self.patchers = [
            patch("shared.utils.trigger_runner.get_trigger_runner", return_value=self.runner),
            patch("server.auth.require_dashboard_auth", return_value=True),
        ]
        for p in self.patchers:
            p.start()
        self.client = TestClient(app)

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def fire(self, body: bytes = b'{"key":"MT-32"}', headers=None, key=FIRE_KEY):
        headers = dict(headers or {})
        headers["Content-Type"] = "application/json"
        if key:
            headers["X-MATE-Trigger-Key"] = key
        return self.client.post("/triggers/7/fire", content=body, headers=headers)


class TestSignatureRequired(FireEndpointTestCase):

    require_signature = True

    def test_github_header_is_accepted(self):
        body = b'{"key":"MT-32"}'
        resp = self.fire(body, {"X-Hub-Signature-256": "sha256=" + sign(body)})
        self.assertEqual(resp.status_code, 200)
        self.runner.execute_trigger.assert_called_once_with(7, {"key": "MT-32"})

    def test_mate_native_header_is_accepted(self):
        body = b'{"key":"MT-32"}'
        resp = self.fire(body, {"X-MATE-Signature": sign(body)})
        self.assertEqual(resp.status_code, 200)

    def test_a_wrong_signature_is_rejected(self):
        resp = self.fire(b'{"key":"MT-32"}', {"X-MATE-Signature": sign(b'{"key":"other"}')})
        self.assertEqual(resp.status_code, 401)
        self.runner.execute_trigger.assert_not_called()

    def test_a_valid_fire_key_without_a_signature_is_rejected(self):
        # The whole point: the key authenticates the caller, it does not prove
        # who composed the body that reaches the prompt.
        resp = self.fire(b'{"key":"MT-32"}')
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Signature required", resp.json()["detail"])
        self.runner.execute_trigger.assert_not_called()

    def test_dashboard_auth_without_a_signature_is_rejected(self):
        resp = self.fire(b'{"key":"MT-32"}', key=None)
        self.assertEqual(resp.status_code, 401)
        self.runner.execute_trigger.assert_not_called()

    def test_verification_runs_over_the_raw_body_before_parsing(self):
        # Not valid JSON: it reaches the signature check first and passes it, so
        # the trigger still fires with no payload rather than failing to verify.
        body = b'not json at all'
        resp = self.fire(body, {"X-MATE-Signature": sign(body)})
        self.assertEqual(resp.status_code, 200)
        self.runner.execute_trigger.assert_called_once_with(7, None)

    def test_a_resigned_reserialisation_of_the_same_json_is_still_the_raw_bytes(self):
        # Signing the bytes actually sent is what is checked — a digest over
        # re-serialised JSON (different whitespace) must not pass.
        body = b'{"key": "MT-32"}'
        reserialised = json.dumps(json.loads(body), separators=(",", ":")).encode()
        self.assertNotEqual(body, reserialised)
        resp = self.fire(body, {"X-MATE-Signature": sign(reserialised)})
        self.assertEqual(resp.status_code, 401)


class TestSignatureNotRequired(FireEndpointTestCase):
    """Triggers without it enabled must behave exactly as they did before."""

    require_signature = False

    def test_fire_key_alone_still_works(self):
        resp = self.fire(b'{"key":"MT-32"}')
        self.assertEqual(resp.status_code, 200)
        self.runner.execute_trigger.assert_called_once_with(7, {"key": "MT-32"})

    def test_an_unsigned_body_is_not_examined(self):
        resp = self.fire(b'{"key":"MT-32"}', {"X-MATE-Signature": "garbage"})
        self.assertEqual(resp.status_code, 200)

    def test_a_wrong_fire_key_is_still_refused(self):
        resp = self.fire(b'{"key":"MT-32"}', key="wrong-key")
        self.assertEqual(resp.status_code, 403)

    def test_dashboard_auth_still_works(self):
        resp = self.fire(b'{"key":"MT-32"}', key=None)
        self.assertEqual(resp.status_code, 200)


class TestTriggerSerialisation(unittest.TestCase):

    def test_to_dict_never_leaks_the_signing_secret(self):
        trigger = AgentTrigger(
            name="t", trigger_type="webhook", agent_name="a", project_id=1,
            prompt="p", signing_secret=SECRET, require_signature=True,
        )
        payload = trigger.to_dict()
        self.assertNotIn("signing_secret", payload)
        self.assertNotIn(SECRET, json.dumps(payload))
        # The dashboard still needs to know a secret exists and whether it is used.
        self.assertTrue(payload["has_signing_secret"])
        self.assertTrue(payload["require_signature"])

    def test_a_trigger_without_a_secret_reports_so(self):
        trigger = AgentTrigger(name="t", trigger_type="cron", agent_name="a",
                               project_id=1, prompt="p")
        payload = trigger.to_dict()
        self.assertFalse(payload["has_signing_secret"])
        self.assertFalse(payload["require_signature"])


if __name__ == "__main__":
    unittest.main()
