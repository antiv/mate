#!/usr/bin/env python3
"""
Tests for reaching external MCP servers over HTTP.

MATE serves its own agents as HTTP MCP servers but the client could only speak
stdio, so a remote server had to be reached by spawning `npx mcp-remote` as a
subprocess — a Node dependency on the host and a process per server, to talk to
a protocol MATE already implements on the other side.

A server config is now HTTP when it carries a `url`, and stdio when it carries
`command`/`args`. The stdio path has to keep working untouched: it is what every
existing installation is configured with.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)

from shared.utils.tools.mcp_tools import create_mcp_tools_from_config


def config(**servers):
    return {"name": "a1", "mcp_servers_config": {"mcpServers": servers}}


def params_of(toolset):
    """The connection params the toolset was built with."""
    return toolset._mcp_session_manager._connection_params


class TestHttpTransport(unittest.TestCase):

    def test_a_url_produces_a_streamable_http_toolset(self):
        tools = create_mcp_tools_from_config(
            config(remote={"url": "https://mcp.example.com/mcp"}))
        self.assertEqual(len(tools), 1)
        params = params_of(tools[0])
        self.assertIsInstance(params, StreamableHTTPConnectionParams)
        self.assertEqual(params.url, "https://mcp.example.com/mcp")

    def test_sse_transport_is_selectable_for_older_servers(self):
        for key in ("transport", "type"):
            with self.subTest(key=key):
                tools = create_mcp_tools_from_config(
                    config(remote={"url": "https://mcp.example.com/sse", key: "sse"}))
                self.assertIsInstance(params_of(tools[0]), SseConnectionParams)

    def test_headers_reach_the_transport(self):
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer s3cret"},
        }))
        self.assertEqual(params_of(tools[0]).headers,
                         {"Authorization": "Bearer s3cret"})

    def test_headers_may_arrive_as_a_json_string(self):
        # Config reaches this function already parsed or still encoded, depending
        # on whether it came from the database or the dashboard.
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp",
            "headers": '{"Authorization": "Bearer s3cret"}',
        }))
        self.assertEqual(params_of(tools[0]).headers,
                         {"Authorization": "Bearer s3cret"})

    def test_timeout_keeps_the_meaning_it_has_for_stdio(self):
        # `timeout` is documented as how long a slow tool may take, so it maps to
        # the read timeout, not to the connect timeout.
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp", "timeout": 300}))
        params = params_of(tools[0])
        self.assertEqual(params.sse_read_timeout, 300.0)
        self.assertEqual(params.timeout, 5.0)

    def test_connect_timeout_is_separately_configurable(self):
        tools = create_mcp_tools_from_config(config(remote={
            "url": "https://mcp.example.com/mcp", "connect_timeout": 20}))
        self.assertEqual(params_of(tools[0]).timeout, 20.0)

    def test_a_non_http_url_is_refused(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "not-a-url"):
            with self.subTest(url=url):
                self.assertEqual(create_mcp_tools_from_config(config(bad={"url": url})), [])

    def test_url_wins_when_a_config_sets_both(self):
        tools = create_mcp_tools_from_config(config(both={
            "url": "https://mcp.example.com/mcp",
            "command": "npx",
            "args": ["mcp-remote", "https://mcp.example.com/mcp"],
        }))
        self.assertEqual(len(tools), 1)
        self.assertIsInstance(params_of(tools[0]), StreamableHTTPConnectionParams)


class TestStdioStillWorks(unittest.TestCase):
    """The path every existing installation is already configured with."""

    def test_command_config_still_produces_a_stdio_toolset(self):
        with patch("shared.utils.tools.mcp_tools.resolve_mcp_command",
                   return_value="/usr/bin/npx"):
            tools = create_mcp_tools_from_config(config(local={
                "command": "npx", "args": ["-y", "some-mcp"], "timeout": 120}))
        self.assertEqual(len(tools), 1)
        params = params_of(tools[0])
        self.assertIsInstance(params, StdioConnectionParams)
        self.assertEqual(params.timeout, 120.0)
        self.assertEqual(params.server_params.command, "/usr/bin/npx")

    def test_args_and_env_may_still_arrive_as_json_strings(self):
        with patch("shared.utils.tools.mcp_tools.resolve_mcp_command",
                   return_value="/usr/bin/npx"):
            tools = create_mcp_tools_from_config(config(local={
                "command": "npx", "args": '["-y", "some-mcp"]', "env": '{"K": "V"}'}))
        params = params_of(tools[0])
        self.assertEqual(params.server_params.args, ["-y", "some-mcp"])
        self.assertEqual(params.server_params.env["K"], "V")

    def test_invalid_args_json_skips_only_that_server(self):
        with patch("shared.utils.tools.mcp_tools.resolve_mcp_command",
                   return_value="/usr/bin/npx"):
            tools = create_mcp_tools_from_config(config(
                broken={"command": "npx", "args": "{not json"},
                fine={"command": "npx", "args": ["-y", "ok"]},
            ))
        self.assertEqual(len(tools), 1)

    def test_a_server_with_neither_url_nor_command_is_skipped(self):
        self.assertEqual(create_mcp_tools_from_config(config(empty={"timeout": 30})), [])

    def test_http_and_stdio_servers_coexist_in_one_config(self):
        with patch("shared.utils.tools.mcp_tools.resolve_mcp_command",
                   return_value="/usr/bin/npx"):
            tools = create_mcp_tools_from_config(config(
                remote={"url": "https://mcp.example.com/mcp"},
                local={"command": "npx", "args": ["-y", "some-mcp"]},
            ))
        self.assertEqual(len(tools), 2)
        kinds = {type(params_of(t)) for t in tools}
        self.assertEqual(kinds, {StreamableHTTPConnectionParams, StdioConnectionParams})


if __name__ == "__main__":
    unittest.main()
