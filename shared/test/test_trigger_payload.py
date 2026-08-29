#!/usr/bin/env python3
"""
Tests for webhook payloads reaching the trigger prompt (#75).

`execute_trigger` took no payload and the fire endpoint never read the body, so
an external system could fire a trigger but not tell it anything — the agent
always saw the same fixed prompt. These tests pin the substitution, the size
cap that keeps an attacker-influenced body from becoming an unbounded prompt,
and that triggers written before payloads existed still render unchanged.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.trigger_runner import (
    MAX_PAYLOAD_CHARS, TriggerRunner, render_prompt,
)


class TestRenderPrompt(unittest.TestCase):

    def test_whole_payload_placeholder(self):
        out = render_prompt("Handle: {{ payload }}", {"a": 1})
        self.assertEqual(out, 'Handle: {"a": 1}')

    def test_field_placeholder(self):
        out = render_prompt("Issue {{ payload.key }} changed", {"key": "MT-32"})
        self.assertEqual(out, "Issue MT-32 changed")

    def test_nested_path(self):
        out = render_prompt("{{ payload.issue.fields.summary }}",
                            {"issue": {"fields": {"summary": "Login broken"}}})
        self.assertEqual(out, "Login broken")

    def test_list_index_in_path(self):
        out = render_prompt("{{ payload.commits.0.id }}",
                            {"commits": [{"id": "abc123"}, {"id": "def456"}]})
        self.assertEqual(out, "abc123")

    def test_missing_path_renders_empty_rather_than_raising(self):
        # A trigger firing with one field absent should still run.
        self.assertEqual(render_prompt("[{{ payload.nope }}]", {"key": "v"}), "[]")

    def test_whitespace_inside_braces_is_tolerated(self):
        self.assertEqual(render_prompt("{{payload.k}}|{{  payload.k  }}", {"k": "x"}), "x|x")

    def test_non_string_values_are_json_encoded(self):
        self.assertEqual(render_prompt("{{ payload.n }}", {"n": 42}), "42")
        self.assertEqual(render_prompt("{{ payload.o }}", {"o": {"k": 1}}), '{"k": 1}')

    def test_prompt_without_placeholder_is_untouched(self):
        prompt = "Summarise today's sales."
        self.assertEqual(render_prompt(prompt, {"anything": 1}), prompt)

    def test_no_payload_leaves_placeholders_alone(self):
        # Cron firings pass nothing; the prompt must not be mangled.
        prompt = "Report {{ payload.k }}"
        self.assertEqual(render_prompt(prompt, None), prompt)

    def test_oversized_value_is_truncated(self):
        out = render_prompt("{{ payload.big }}", {"big": "x" * (MAX_PAYLOAD_CHARS + 5000)})
        self.assertLess(len(out), MAX_PAYLOAD_CHARS + 200)
        self.assertIn("truncated", out)

    def test_oversized_whole_payload_is_truncated(self):
        out = render_prompt("{{ payload }}", {"big": "x" * (MAX_PAYLOAD_CHARS + 5000)})
        self.assertLess(len(out), MAX_PAYLOAD_CHARS + 200)

    def test_payload_text_is_not_itself_treated_as_a_template(self):
        # A body containing a placeholder must not trigger a second substitution.
        out = render_prompt("{{ payload.k }}", {"k": "{{ payload.secret }}"})
        self.assertEqual(out, "{{ payload.secret }}")


class TestExecuteTriggerThreadsPayload(unittest.TestCase):

    def setUp(self):
        self.runner = TriggerRunner()
        self.trigger = MagicMock(
            id=1, trigger_type="webhook", agent_name="a1",
            prompt="Issue {{ payload.key }} needs attention",
        )
        self.runner._route_output = MagicMock()

    def test_payload_reaches_the_agent_prompt(self):
        self.runner._invoke_agent = MagicMock(return_value="done")
        self.runner._execute_trigger_sync(self.trigger, {"key": "MT-99"})
        self.runner._invoke_agent.assert_called_once_with(
            "a1", "Issue MT-99 needs attention")

    def test_cron_firing_passes_no_payload(self):
        self.runner._invoke_agent = MagicMock(return_value="done")
        self.runner._execute_trigger_sync(self.trigger)
        self.runner._invoke_agent.assert_called_once_with(
            "a1", "Issue {{ payload.key }} needs attention")


class TestFireEndpointReadsTheBody(unittest.TestCase):
    """The endpoint used to discard the body entirely."""

    def test_webhook_fire_passes_the_body_through(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from pathlib import Path
        from shared.utils.dashboard.dashboard_server import DashboardServer

        with patch("shared.utils.database_client.get_database_client",
                   return_value=MagicMock()):
            project_root = Path(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))))
            app = FastAPI()
            server = DashboardServer(app, project_root)

        trigger_row = MagicMock(id=7, fire_key_hash=None)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = trigger_row
        server.db_client = MagicMock()
        server.db_client.get_session.return_value = session

        runner = MagicMock()
        runner.execute_trigger.return_value = {"status": "ok", "agent_response": "x"}

        with patch("shared.utils.trigger_runner.get_trigger_runner", return_value=runner), \
             patch("server.auth.require_dashboard_auth", return_value=True):
            client = TestClient(app)
            body = {"key": "MT-1", "action": "updated"}
            resp = client.post("/triggers/7/fire", json=body)

        self.assertEqual(resp.status_code, 200)
        runner.execute_trigger.assert_called_once_with(7, body)


if __name__ == "__main__":
    unittest.main()
