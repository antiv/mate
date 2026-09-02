#!/usr/bin/env python3
"""
Tests for ${VAR} interpolation in MCP server config.

The dashboard promised this for years without implementing it, so a token pasted
as ${AUTH_TOKEN} was sent to the remote server as the literal seven-character
string. Credentials also should not have to be stored in an agent config row to
be usable.

An unset variable skips the server. The alternatives are worse in both
directions: substituting empty sends an unauthenticated request that may
succeed with reduced scope, and leaving the placeholder sends `${VAR}` as the
credential.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

from shared.utils.tools.mcp_tools import create_mcp_tools_from_config


def config(**servers):
    return {"name": "a1", "mcp_servers_config": {"mcpServers": servers}}


def params_of(toolset):
    return toolset._mcp_session_manager._connection_params


class TestEnvInterpolation(unittest.TestCase):

    def test_a_header_secret_is_read_from_the_environment(self):
        with patch.dict(os.environ, {"AUTH_TOKEN": "s3cret"}):
            tools = create_mcp_tools_from_config(config(remote={
                "url": "https://mcp.example.com/mcp",
                "headers": {"Authorization": "Bearer ${AUTH_TOKEN}"},
            }))
        self.assertEqual(params_of(tools[0]).headers,
                         {"Authorization": "Bearer s3cret"})

    def test_the_placeholder_may_sit_inside_a_url(self):
        with patch.dict(os.environ, {"MCP_HOST": "mcp.example.com"}):
            tools = create_mcp_tools_from_config(config(remote={
                "url": "https://${MCP_HOST}/mcp"}))
        self.assertEqual(params_of(tools[0]).url, "https://mcp.example.com/mcp")

    def test_several_placeholders_in_one_string(self):
        with patch.dict(os.environ, {"A": "one", "B": "two"}):
            tools = create_mcp_tools_from_config(config(remote={
                "url": "https://example.com/${A}/${B}"}))
        self.assertEqual(params_of(tools[0]).url, "https://example.com/one/two")

    def test_stdio_args_and_env_are_interpolated_too(self):
        # The documented Tavily example carries its API key inside args.
        with patch.dict(os.environ, {"TAVILY_KEY": "tvly-123", "REGION": "eu"}):
            with patch("shared.utils.tools.mcp_tools.resolve_mcp_command",
                       return_value="/usr/bin/npx"):
                tools = create_mcp_tools_from_config(config(local={
                    "command": "npx",
                    "args": ["mcp-remote", "https://mcp.tavily.com/?key=${TAVILY_KEY}"],
                    "env": {"REGION": "${REGION}"},
                }))
        params = params_of(tools[0])
        self.assertIsInstance(params, StdioConnectionParams)
        self.assertEqual(params.server_params.args[1],
                         "https://mcp.tavily.com/?key=tvly-123")
        self.assertEqual(params.server_params.env["REGION"], "eu")

    def test_an_unset_variable_skips_the_server(self):
        # The bug this replaces sent `${AUTH_TOKEN}` itself as the credential.
        os.environ.pop("DEFINITELY_NOT_SET", None)
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer ${DEFINITELY_NOT_SET}"},
        }))
        self.assertEqual(tools, [])

    def test_an_unset_variable_skips_only_that_server(self):
        os.environ.pop("DEFINITELY_NOT_SET", None)
        tools = create_mcp_tools_from_config(config(
            broken={"url": "https://a.example.com/mcp",
                    "headers": {"Authorization": "${DEFINITELY_NOT_SET}"}},
            fine={"url": "https://b.example.com/mcp"},
        ))
        self.assertEqual(len(tools), 1)
        self.assertEqual(params_of(tools[0]).url, "https://b.example.com/mcp")

    def test_an_empty_variable_is_a_value_not_a_miss(self):
        # Set-but-empty is a deliberate choice by whoever set it.
        with patch.dict(os.environ, {"EMPTY_ON_PURPOSE": ""}):
            tools = create_mcp_tools_from_config(config(remote={
                "url": "https://mcp.example.com/mcp",
                "headers": {"X-Key": "${EMPTY_ON_PURPOSE}"},
            }))
        self.assertEqual(params_of(tools[0]).headers, {"X-Key": ""})

    def test_config_without_placeholders_is_untouched(self):
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer literal-token"},
        }))
        self.assertEqual(params_of(tools[0]).headers,
                         {"Authorization": "Bearer literal-token"})

    def test_a_secret_containing_a_quote_survives_a_json_string_field(self):
        # Interpolation happens after JSON parsing precisely so this cannot
        # corrupt the surrounding document.
        with patch.dict(os.environ, {"WEIRD": 'a"b'}):
            tools = create_mcp_tools_from_config(config(remote={
                "url": "https://mcp.example.com/mcp",
                "headers": '{"X-Key": "${WEIRD}"}',
            }))
        self.assertEqual(params_of(tools[0]).headers, {"X-Key": 'a"b'})

    def test_a_dollar_without_braces_is_left_alone(self):
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp",
            "headers": {"X-Key": "$HOME and $100"},
        }))
        self.assertEqual(params_of(tools[0]).headers, {"X-Key": "$HOME and $100"})


if __name__ == "__main__":
    unittest.main()
