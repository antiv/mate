#!/usr/bin/env python3
"""
Unit tests for the trigger engine (shared.utils.trigger_runner).

Triggers run unattended on a scheduler, so failures are invisible until something
quietly stops firing. These cover the webhook key boundary and error isolation.
"""

import os
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.trigger_runner import TriggerRunner, generate_webhook_path


class TestGenerateWebhookPath(unittest.TestCase):

    def test_slug_is_lowercased_and_sanitized(self):
        path = generate_webhook_path("Daily Report!")
        self.assertTrue(path.startswith("daily-report-"))
        self.assertRegex(path, r"^[a-z0-9-]+$")

    def test_paths_are_unique_for_the_same_name(self):
        paths = {generate_webhook_path("report") for _ in range(20)}
        self.assertEqual(len(paths), 20)

    def test_empty_name_falls_back_to_default_slug(self):
        self.assertTrue(generate_webhook_path("   ").startswith("trigger-"))

    def test_name_of_only_symbols_falls_back_to_default_slug(self):
        self.assertTrue(generate_webhook_path("!!!").startswith("trigger-"))

    def test_long_name_is_truncated(self):
        path = generate_webhook_path("a" * 200)
        # 40-char slug cap plus the "-" separator and 8 hex chars.
        self.assertLessEqual(len(path), 40 + 1 + 8)


class TestFireKeys(unittest.TestCase):
    """A webhook fire key is a credential: only the hash is stored."""

    def test_generate_returns_raw_and_hash(self):
        raw, hashed = TriggerRunner.generate_fire_key()
        self.assertIsInstance(raw, str)
        self.assertIsInstance(hashed, str)
        self.assertNotEqual(raw, hashed)
        self.assertTrue(raw)

    def test_generated_keys_are_unique(self):
        keys = {TriggerRunner.generate_fire_key()[0] for _ in range(20)}
        self.assertEqual(len(keys), 20)

    def test_verify_accepts_the_matching_key(self):
        raw, hashed = TriggerRunner.generate_fire_key()
        self.assertTrue(TriggerRunner.verify_fire_key(raw, hashed))

    def test_verify_rejects_a_different_key(self):
        _, hashed = TriggerRunner.generate_fire_key()
        other_raw, _ = TriggerRunner.generate_fire_key()
        self.assertFalse(TriggerRunner.verify_fire_key(other_raw, hashed))

    def test_verify_rejects_the_hash_itself_as_the_key(self):
        raw, hashed = TriggerRunner.generate_fire_key()
        self.assertFalse(TriggerRunner.verify_fire_key(hashed, hashed))


class TestExecuteTriggerSync(unittest.TestCase):
    """One misbehaving trigger must return an error, never propagate an exception."""

    def setUp(self):
        self.runner = TriggerRunner()

    def _trigger(self, trigger_type="cron"):
        return SimpleNamespace(
            id=1, trigger_type=trigger_type, agent_name="reporter", prompt="go"
        )

    def test_unimplemented_types_are_skipped_not_run(self):
        for trigger_type in ("file_watch", "event_bus"):
            with self.subTest(trigger_type=trigger_type):
                with patch.object(self.runner, "_invoke_agent") as invoke:
                    result = self.runner._execute_trigger_sync(self._trigger(trigger_type))
                self.assertEqual(result["status"], "skipped")
                self.assertIn("not yet implemented", result["message"])
                invoke.assert_not_called()

    def test_successful_run_returns_agent_response(self):
        with patch.object(self.runner, "_invoke_agent", return_value="done"), \
             patch.object(self.runner, "_route_output") as route:
            result = self.runner._execute_trigger_sync(self._trigger())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["agent_response"], "done")
        route.assert_called_once()

    def test_agent_failure_is_contained(self):
        with patch.object(self.runner, "_invoke_agent", side_effect=RuntimeError("boom")):
            result = self.runner._execute_trigger_sync(self._trigger())
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["message"])

    def test_output_routing_failure_preserves_agent_response(self):
        with patch.object(self.runner, "_invoke_agent", return_value="partial"), \
             patch.object(self.runner, "_route_output", side_effect=RuntimeError("smtp down")):
            result = self.runner._execute_trigger_sync(self._trigger())
        self.assertEqual(result["status"], "error")
        self.assertIn("smtp down", result["message"])
        self.assertEqual(result["agent_response"], "partial")


if __name__ == "__main__":
    unittest.main()
