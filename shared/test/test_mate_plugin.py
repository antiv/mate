#!/usr/bin/env python3
"""
Unit tests for the app-wide MATE plugin (ADK Plugins migration).
"""

import asyncio
import os
import unittest
from unittest.mock import Mock, patch
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.callbacks.mate_plugin import MatePlugin


class TestMatePlugin(unittest.TestCase):

    def test_plugin_instantiation(self):
        """Test that MatePlugin is a valid ADK BasePlugin."""
        from google.adk.plugins.base_plugin import BasePlugin
        plugin = MatePlugin()
        self.assertIsInstance(plugin, BasePlugin)
        self.assertEqual(plugin.name, 'mate_plugin')

    @patch('shared.callbacks.mate_plugin.combined_user_profile_and_rbac_callback')
    def test_before_model_delegates_to_callback_chain(self, mock_combined):
        """Test that before_model_callback delegates to the existing chain."""
        mock_combined.return_value = None
        plugin = MatePlugin()
        ctx, request = Mock(), Mock()

        result = asyncio.run(plugin.before_model_callback(
            callback_context=ctx, llm_request=request))

        mock_combined.assert_called_once_with(ctx, request)
        self.assertIsNone(result)

    @patch('shared.callbacks.mate_plugin.log_token_usage_callback')
    @patch('shared.callbacks.mate_plugin.guardrail_after_model_callback')
    def test_after_model_guardrail_block_still_logs_tokens(self, mock_guardrail, mock_log):
        """Test that a guardrail-replaced response is returned and token usage logged."""
        blocked_response = Mock()
        mock_guardrail.return_value = blocked_response
        mock_log.return_value = None
        plugin = MatePlugin()
        ctx, response = Mock(), Mock()

        result = asyncio.run(plugin.after_model_callback(
            callback_context=ctx, llm_response=response))

        self.assertIs(result, blocked_response)
        mock_log.assert_called_once_with(ctx, blocked_response)

    @patch('shared.callbacks.mate_plugin.log_token_usage_callback')
    @patch('shared.callbacks.mate_plugin.guardrail_after_model_callback')
    def test_after_model_passthrough_logs_tokens(self, mock_guardrail, mock_log):
        """Test that a clean response passes through with token logging."""
        mock_guardrail.return_value = None
        mock_log.return_value = None
        plugin = MatePlugin()
        ctx, response = Mock(), Mock()

        result = asyncio.run(plugin.after_model_callback(
            callback_context=ctx, llm_response=response))

        self.assertIsNone(result)
        mock_log.assert_called_once_with(ctx, response)


class TestPluginModeAgentCallbacks(unittest.TestCase):

    @patch('shared.utils.tools.tool_factory.ToolFactory')
    @patch('shared.utils.utils.create_model')
    def test_llm_agent_skips_callbacks_in_plugin_mode(self, mock_create_model, mock_tool_factory):
        """Test that per-agent model callbacks are not attached when MATE_PLUGINS_ENABLED."""
        from shared.utils.agent_manager import AgentManager

        mock_tool_factory.return_value.create_tools.return_value = []
        mock_create_model.return_value = 'gemini-2.5-flash'
        manager = AgentManager()
        manager.db_client = Mock()
        config = {
            'name': 'test_plugin_mode_agent',
            'type': 'llm',
            'description': 'Test agent',
            'instruction': 'Test instruction',
            'model_name': 'gemini-2.5-flash',
            'tool_config': None,
            'max_iterations': None,
            'allowed_for_roles': []
        }

        with patch.dict(os.environ, {'MATE_PLUGINS_ENABLED': 'true'}):
            agent = manager._initialize_agent(config, [])
        self.assertIsNotNone(agent)
        self.assertIsNone(agent.before_model_callback)
        self.assertIsNone(agent.after_model_callback)

        manager.clear_initialized_agents()

        with patch.dict(os.environ, {'MATE_PLUGINS_ENABLED': 'false'}):
            agent = manager._initialize_agent(config, [])
        self.assertIsNotNone(agent)
        self.assertIsNotNone(agent.before_model_callback)
        self.assertIsNotNone(agent.after_model_callback)


if __name__ == '__main__':
    unittest.main()
