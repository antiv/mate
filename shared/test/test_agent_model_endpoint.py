#!/usr/bin/env python3
"""
Tests for per-agent OpenAI-compatible endpoints.

An external agent reached over /v1/chat/completions is, on the wire,
indistinguishable from a model — so pointing MATE at an agent you already run
needs no proxy agent class, only somewhere to put the endpoint. Before this,
base_url and api_key came from provider env vars alone, so every agent shared one
endpoint per provider.

The security-relevant case is an unresolvable ${VAR}. Falling back to the
provider's env key would send that key to whatever third-party host base_url
names, so the agent refuses to build instead.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.utils import resolve_agent_endpoint, resolve_env_placeholders


class TestResolveEnvPlaceholders(unittest.TestCase):
    """The shared helper, used by both MCP config and agent endpoints."""

    def test_reports_missing_names_rather_than_guessing(self):
        os.environ.pop("NOT_SET_ANYWHERE", None)
        resolved, missing = resolve_env_placeholders("Bearer ${NOT_SET_ANYWHERE}")
        self.assertEqual(missing, {"NOT_SET_ANYWHERE"})
        # The placeholder survives so a caller cannot mistake it for a real value.
        self.assertEqual(resolved, "Bearer ${NOT_SET_ANYWHERE}")

    def test_recurses_through_lists_and_dicts(self):
        with patch.dict(os.environ, {"V": "x"}):
            resolved, missing = resolve_env_placeholders(
                {"a": ["${V}", {"b": "${V}"}], "c": 7})
        self.assertEqual(resolved, {"a": ["x", {"b": "x"}], "c": 7})
        self.assertEqual(missing, set())

    def test_leaves_a_bare_dollar_name_alone(self):
        resolved, missing = resolve_env_placeholders("$HOME costs $100")
        self.assertEqual(resolved, "$HOME costs $100")
        self.assertEqual(missing, set())


class TestResolveAgentEndpoint(unittest.TestCase):

    def test_an_agent_without_an_endpoint_is_unaffected(self):
        self.assertEqual(resolve_agent_endpoint({"name": "a", "model_name": "openai/gpt-4o"}),
                         (None, None))

    def test_the_endpoint_is_read_from_the_row(self):
        base_url, api_key = resolve_agent_endpoint({
            "name": "a", "model_name": "openai/my-agent",
            "model_base_url": "https://agent.example.com/v1",
            "model_api_key": "sk-literal",
        })
        self.assertEqual(base_url, "https://agent.example.com/v1")
        self.assertEqual(api_key, "sk-literal")

    def test_the_key_may_live_in_the_environment(self):
        with patch.dict(os.environ, {"MY_AGENT_KEY": "sk-from-env"}):
            _, api_key = resolve_agent_endpoint({
                "name": "a", "model_name": "openai/my-agent",
                "model_base_url": "https://agent.example.com/v1",
                "model_api_key": "${MY_AGENT_KEY}",
            })
        self.assertEqual(api_key, "sk-from-env")

    def test_an_unresolvable_key_refuses_rather_than_falling_back(self):
        # Falling back would send the provider's own key to agent.example.com.
        os.environ.pop("NOT_SET_ANYWHERE", None)
        with self.assertRaises(ValueError) as caught:
            resolve_agent_endpoint({
                "name": "a", "model_name": "openai/my-agent",
                "model_base_url": "https://agent.example.com/v1",
                "model_api_key": "${NOT_SET_ANYWHERE}",
            })
        self.assertIn("NOT_SET_ANYWHERE", str(caught.exception))

    def test_a_keyless_endpoint_does_not_inherit_the_provider_key(self):
        # LiteLLM would otherwise read OPENAI_API_KEY and send it to the host
        # base_url names. A placeholder keeps it at home.
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-mine"}):
            _, api_key = resolve_agent_endpoint({
                "name": "a", "model_name": "openai/local",
                "model_base_url": "http://localhost:9000/v1",
            })
        self.assertIsNotNone(api_key)
        self.assertNotEqual(api_key, "sk-mine")

    def test_a_base_url_on_a_native_gemini_model_warns(self):
        # create_model routes bare gemini names to the native backend, which
        # ignores base_url — silently, without this.
        with self.assertLogs("shared.utils.utils", level="WARNING") as logs:
            resolve_agent_endpoint({
                "name": "a", "model_name": "gemini-2.5-flash",
                "model_base_url": "https://agent.example.com/v1",
            })
        self.assertIn("ignores it", "\n".join(logs.output))

    def test_no_warning_when_the_model_carries_a_provider_prefix(self):
        with patch("shared.utils.utils.logger") as log:
            resolve_agent_endpoint({
                "name": "a", "model_name": "openai/my-agent",
                "model_base_url": "https://agent.example.com/v1",
            })
        log.warning.assert_not_called()


class TestModelBuiltFromConfig(unittest.TestCase):

    def test_the_endpoint_reaches_create_model(self):
        from shared.utils import utils
        with patch.object(utils, "create_model") as create:
            utils.create_model_from_agent_config({
                "name": "a", "model_name": "openai/my-agent",
                "model_base_url": "https://agent.example.com/v1",
                "model_api_key": "sk-literal",
            })
        create.assert_called_once_with(
            model_name="openai/my-agent",
            api_key="sk-literal",
            base_url="https://agent.example.com/v1",
        )

    def test_an_ordinary_agent_still_passes_no_endpoint(self):
        from shared.utils import utils
        with patch.object(utils, "create_model") as create:
            utils.create_model_from_agent_config({"name": "a", "model_name": "gemini-2.5-flash"})
        create.assert_called_once_with(
            model_name="gemini-2.5-flash", api_key=None, base_url=None)


class TestStoredKeyMasking(unittest.TestCase):
    """A literal key must not reach the browser, but must survive a form round trip."""

    def test_a_literal_key_is_masked(self):
        from shared.utils.dashboard.dashboard_server import mask_api_key, STORED_SECRET_SENTINEL
        self.assertEqual(mask_api_key("sk-abc123"), STORED_SECRET_SENTINEL)

    def test_an_env_reference_is_not_a_secret_and_goes_out_as_written(self):
        from shared.utils.dashboard.dashboard_server import mask_api_key
        self.assertEqual(mask_api_key("${MY_AGENT_KEY}"), "${MY_AGENT_KEY}")

    def test_nothing_stored_stays_nothing(self):
        from shared.utils.dashboard.dashboard_server import mask_api_key
        self.assertIsNone(mask_api_key(None))
        self.assertEqual(mask_api_key(""), "")


if __name__ == "__main__":
    unittest.main()
