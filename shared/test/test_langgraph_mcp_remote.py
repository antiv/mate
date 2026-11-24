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
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.langgraph import mcp_client


def _config(**servers):
    return {"mcpServers": servers}


class TestConnections(unittest.TestCase):

    def _connections(self, config, failing=()):
        """The connections the client opened, by server, and the tools it got."""
        connections = {}
        entries = config["mcpServers"]

        async def server_tools(connection):
            # A server is known by its url, or its command
            name = next(n for n, e in entries.items() if n not in connections and (
                e.get("url") == connection["url"] if "url" in connection else "command" in e))
            connections[name] = connection
            if name in failing:
                raise ConnectionError("unreachable")
            return [f"{name}_tool"]

        with patch.object(mcp_client, "_server_tools", server_tools):
            tools = asyncio.run(mcp_client.create_mcp_tools(config, agent_name="a1"))
        return connections, tools

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
        with self.assertLogs("shared.utils.langgraph.mcp_client", "ERROR"):
            _, tools = self._connections(_config(down={"url": "https://down/mcp"}, up={"url": "https://up/mcp"}),
                                         failing=("down",))
        self.assertEqual(tools, ["up_tool"])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


STDIO_SERVER = """
import os
from mcp.server.mcpserver import MCPServer
server = MCPServer("stdio")

@server.tool()
def greet(name: str) -> str:
    return f"{os.environ['GREETING']}, {name}"

server.run()
"""


class TestAgainstARealServer(unittest.TestCase):
    """The SDK's server on a local port, or over stdio, reached the way an agent reaches it."""

    @classmethod
    def setUpClass(cls):
        import uvicorn
        from mcp.server.mcpserver import Context, Image, MCPServer
        from mcp.server.mcpserver.exceptions import ToolError

        cls.servers = []
        cls.ports = {}
        for transport in ("streamable_http", "sse"):
            mcp = MCPServer("echo")

            @mcp.tool()
            def echo(text: str) -> str:
                """Echo the text back."""
                return f"echo: {text}"

            @mcp.tool()
            def fail() -> str:
                raise ToolError("no such order")

            @mcp.tool()
            def picture(size: int) -> Image:
                return Image(data=b"x" * size, format="png")

            @mcp.tool()
            def header(name: str, ctx: Context) -> str:
                return ctx.request_context.request.headers.get(name, "")

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

    def _entry(self, transport, **more):
        path = "mcp" if transport == "streamable_http" else "sse"
        return {"url": f"http://127.0.0.1:{self.ports[transport]}/{path}", "transport": transport, **more}

    def _call(self, entry, tool, arguments):
        """The tool's message, as the graph's tool node gets it."""
        async def run():
            tools = {t.name: t for t in await mcp_client.create_mcp_tools(_config(remote=entry), agent_name="a1")}
            return await tools[tool].ainvoke({"type": "tool_call", "id": "1", "name": tool, "args": arguments})
        return asyncio.run(run())

    def test_tools_keep_their_names_descriptions_and_schemas(self):
        async def run():
            servers = _config(one=self._entry("streamable_http"), two=self._entry("sse"))
            return await mcp_client.create_mcp_tools(servers, agent_name="a1")
        tools = asyncio.run(run())
        # No server's name in front, with several servers either
        self.assertEqual(sorted(t.name for t in tools), sorted(["echo", "fail", "picture", "header"] * 2))
        echo = next(t for t in tools if t.name == "echo")
        self.assertEqual(echo.description, "Echo the text back.")
        self.assertEqual(echo.args["text"]["type"], "string")

    def test_both_transports(self):
        for transport in ("streamable_http", "sse"):
            message = self._call(self._entry(transport), "echo", {"text": "hi"})
            self.assertEqual((message.status, message.content[0]["text"]), ("success", "echo: hi"))

    def test_headers_reach_the_server(self):
        for transport in ("streamable_http", "sse"):
            message = self._call(self._entry(transport, headers={"X-Key": "k1"}), "header", {"name": "x-key"})
            self.assertEqual(message.content[0]["text"], "k1")

    def test_an_error_of_the_tool_is_a_failed_call(self):
        message = self._call(self._entry("streamable_http"), "fail", {})
        self.assertEqual(message.status, "error")
        self.assertIn("no such order", message.content[0]["text"])

    def test_an_image_larger_than_the_sdks_limit(self):
        message = self._call(self._entry("streamable_http"), "picture", {"size": 2_000_000})
        self.assertEqual((message.content[0]["type"], message.content[0]["mime_type"]), ("image", "image/png"))
        self.assertGreater(len(message.content[0]["base64"]), 2_000_000)

    def test_a_server_that_is_gone_raises(self):
        gone = mcp_client._http_connection("remote", {"url": f"http://127.0.0.1:{_free_port()}/mcp"}, "a1")
        opened = mcp_client._open_session

        async def run():
            config = _config(remote=self._entry("streamable_http"))
            tools = {t.name: t for t in await mcp_client.create_mcp_tools(config, agent_name="a1")}
            with patch.object(mcp_client, "_open_session", lambda _: opened(gone)):
                await tools["echo"].ainvoke({"text": "hi"})
        with self.assertRaises(Exception) as raised:
            asyncio.run(run())
        self.assertNotIsInstance(raised.exception, AssertionError)

    def test_stdio(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py") as script:
            script.write(STDIO_SERVER)
            script.flush()
            with patch.dict(os.environ, {"MCP_GREETING": "Zdravo"}):
                message = self._call({"command": sys.executable, "args": [script.name],
                                      "env": {"GREETING": "${MCP_GREETING}"}}, "greet", {"name": "Ana"})
        self.assertEqual(message.content[0]["text"], "Zdravo, Ana")

    def test_a_redirect_to_another_origin_is_not_followed(self):
        hits = []

        class Elsewhere(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        other = ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)
        target = f"http://127.0.0.1:{other.server_port}/mcp"

        class Redirect(Elsewhere):
            def do_POST(self):
                self.send_response(307)
                self.send_header("location", target)
                self.end_headers()

        first = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        for server in (other, first):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

        async def run():
            return await mcp_client.create_mcp_tools(
                _config(remote={"url": f"http://127.0.0.1:{first.server_port}/mcp"}), agent_name="a1")
        with self.assertLogs("shared.utils.langgraph.mcp_client", "ERROR"):
            self.assertEqual(asyncio.run(run()), [])
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
