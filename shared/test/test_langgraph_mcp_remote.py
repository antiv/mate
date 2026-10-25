#!/usr/bin/env python3
"""
Remote (url) MCP servers on the LangGraph runtime (#289).

The LangGraph runtime took only stdio servers: an entry with a `url` was skipped
with "missing command or args", and the agent ran without its tools. It now
follows ADK (shared/utils/tools/mcp_tools.py): streamable HTTP by default, SSE
when `transport` is "sse", headers with ${VAR} resolved (an unset one skips the
server), `timeout` and `connect_timeout`, http(s) only, and url over command.

The end-to-end tests start a real MCP server on a local port and call its tool.
"""

import asyncio
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.langgraph import mcp_client


def _config(**servers):
    return {"mcpServers": servers}


class _Client:
    """Stands in for MultiServerMCPClient and records the connections it gets."""
    connections = None
    failing = ()

    def __init__(self, connections):
        type(self).connections = connections

    async def get_tools(self, server_name=None):
        if server_name in self.failing:
            raise ConnectionError("unreachable")
        return [f"{server_name}_tool"]


class TestConnections(unittest.TestCase):

    def _connections(self, config):
        _Client.connections, _Client.failing = None, ()
        with patch("langchain_mcp_adapters.client.MultiServerMCPClient", _Client):
            tools = asyncio.run(mcp_client.create_mcp_tools(config, agent_name="a1"))
        return _Client.connections or {}, tools

    def test_a_url_is_streamable_http_by_default(self):
        connections, tools = self._connections(_config(remote={"url": "https://mcp.example.com/mcp"}))
        self.assertEqual(connections["remote"], {
            "transport": "streamable_http", "url": "https://mcp.example.com/mcp",
            "headers": None, "timeout": 5.0, "sse_read_timeout": 60.0})
        self.assertEqual(tools, ["remote_tool"])

    def test_sse_by_transport_or_type(self):
        for key in ("transport", "type"):
            connections, _ = self._connections(_config(remote={"url": "http://h/sse", key: "sse"}))
            self.assertEqual(connections["remote"]["transport"], "sse")

    def test_timeouts_are_honoured(self):
        connections, _ = self._connections(_config(remote={
            "url": "https://h/mcp", "timeout": 300, "connect_timeout": 10}))
        self.assertEqual(connections["remote"]["sse_read_timeout"], 300.0)
        self.assertEqual(connections["remote"]["timeout"], 10.0)

    def test_header_secrets_come_from_the_environment(self):
        with patch.dict(os.environ, {"MCP_TOKEN": "s3cret"}):
            connections, _ = self._connections(_config(remote={
                "url": "https://h/mcp", "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}}))
        self.assertEqual(connections["remote"]["headers"], {"Authorization": "Bearer s3cret"})

    def test_headers_given_as_a_json_string(self):
        connections, _ = self._connections(_config(remote={
            "url": "https://h/mcp", "headers": '{"X-Key": "k"}'}))
        self.assertEqual(connections["remote"]["headers"], {"X-Key": "k"})

    def test_an_unset_variable_skips_the_server(self):
        os.environ.pop("MCP_TOKEN_NOT_SET", None)
        with self.assertLogs("shared.utils.tools.mcp_tools", "ERROR"):
            connections, tools = self._connections(_config(
                remote={"url": "https://h/mcp", "headers": {"Authorization": "Bearer ${MCP_TOKEN_NOT_SET}"}},
                other={"url": "https://other/mcp"}))
        self.assertNotIn("remote", connections)
        self.assertEqual(tools, ["other_tool"])

    def test_a_scheme_other_than_http_is_refused(self):
        for url in ("file:///etc/passwd", "ftp://h/mcp", "h/mcp"):
            with self.assertLogs("shared.utils.langgraph.mcp_client", "ERROR"):
                connections, tools = self._connections(_config(remote={"url": url}))
            self.assertEqual((connections, tools), ({}, []))

    def test_url_wins_over_command(self):
        with self.assertLogs("shared.utils.langgraph.mcp_client", "WARNING") as logs:
            connections, _ = self._connections(_config(remote={
                "url": "https://h/mcp", "command": "npx", "args": ["mcp-remote"]}))
        self.assertEqual(connections["remote"]["transport"], "streamable_http")
        self.assertIn("ignoring command", " ".join(logs.output))

    def test_stdio_entries_still_work(self):
        connections, _ = self._connections(_config(local={"command": sys.executable, "args": ["-V"]}))
        self.assertEqual(connections["local"]["transport"], "stdio")

    def test_stdio_env_placeholders_are_resolved(self):
        with patch.dict(os.environ, {"FS_ROOT": "/data", "FS_TOKEN": "t0k"}):
            connections, _ = self._connections(_config(local={
                "command": sys.executable, "args": ["-m", "server", "${FS_ROOT}"],
                "env": {"TOKEN": "${FS_TOKEN}"}}))
        self.assertEqual(connections["local"]["args"], ["-m", "server", "/data"])
        self.assertEqual(connections["local"]["env"]["TOKEN"], "t0k")

    def test_an_unset_stdio_variable_skips_the_server(self):
        os.environ.pop("FS_TOKEN_NOT_SET", None)
        with self.assertLogs("shared.utils.tools.mcp_tools", "ERROR"):
            connections, _ = self._connections(_config(local={
                "command": sys.executable, "args": ["-V"], "env": {"TOKEN": "${FS_TOKEN_NOT_SET}"}}))
        self.assertEqual(connections, {})

    def test_an_entry_with_neither_is_skipped(self):
        with self.assertLogs("shared.utils.langgraph.mcp_client", "WARNING"):
            connections, tools = self._connections(_config(broken={"timeout": 5}))
        self.assertEqual((connections, tools), ({}, []))

    def test_a_server_that_is_down_costs_only_its_own_tools(self):
        _Client.failing = ("down",)

        async def run():
            with patch("langchain_mcp_adapters.client.MultiServerMCPClient", _Client):
                return await mcp_client.create_mcp_tools(
                    _config(down={"url": "https://down/mcp"}, up={"url": "https://up/mcp"}), "a1")
        with self.assertLogs("shared.utils.langgraph.mcp_client", "ERROR"):
            self.assertEqual(asyncio.run(run()), ["up_tool"])
        _Client.failing = ()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestAgainstARealServer(unittest.TestCase):
    """A FastMCP server on a local port, reached the way an agent reaches it."""

    @classmethod
    def setUpClass(cls):
        import uvicorn
        from mcp.server.fastmcp import FastMCP

        cls.servers = []
        cls.ports = {}
        for transport in ("streamable_http", "sse"):
            mcp = FastMCP("echo")

            @mcp.tool()
            def echo(text: str) -> str:
                """Echo the text back."""
                return f"echo: {text}"

            app = mcp.streamable_http_app() if transport == "streamable_http" else mcp.sse_app()
            port = _free_port()
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
            threading.Thread(target=server.run, daemon=True).start()
            cls.servers.append(server)
            cls.ports[transport] = port
        for port in cls.ports.values():
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                    break
                except OSError:
                    time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.should_exit = True

    def _call_echo(self, entry):
        async def run():
            tools = await mcp_client.create_mcp_tools(_config(remote=entry), agent_name="a1")
            self.assertEqual([t.name for t in tools], ["echo"])
            return await tools[0].ainvoke({"text": "hi"})
        return asyncio.run(run())

    def test_streamable_http(self):
        port = self.ports["streamable_http"]
        result = self._call_echo({"url": f"http://127.0.0.1:{port}/mcp",
                                  "headers": {"Authorization": "Bearer x"}})
        self.assertIn("echo: hi", str(result))

    def test_sse(self):
        port = self.ports["sse"]
        result = self._call_echo({"url": f"http://127.0.0.1:{port}/sse", "transport": "sse"})
        self.assertIn("echo: hi", str(result))


if __name__ == "__main__":
    unittest.main()
