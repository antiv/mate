#!/usr/bin/env python3
"""
The size of what an MCP server streams to an agent, measured on both runtimes' clients:
over the SDK's 1 MB it arrives, over MATE's 16 MB it is an error (shared/utils/mcp_limits.py).
"""

import asyncio
import os
import socket
import sys
import threading
import time
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.utils.langgraph import mcp_client
from shared.utils.mcp_limits import MAX_EVENT_BYTES
from shared.utils.tools.mcp_tools import create_mcp_toolset_http

LARGE = 3_000_000
TOO_LARGE = MAX_EVENT_BYTES + 1000


class TestMcpEventLimit(unittest.TestCase):
    urls: Dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        import uvicorn
        from mcp.server.mcpserver import MCPServer

        for transport in ("streamable_http", "sse"):
            server = MCPServer("sizes")

            # Without structured output, the text is in the event once
            @server.tool(structured_output=False)
            def sized(size: int) -> str:
                return "x" * size

            app = server.sse_app() if transport == "sse" else server.streamable_http_app()
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            web = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
            threading.Thread(target=web.run, daemon=True).start()
            cls.addClassCleanup(setattr, web, "should_exit", True)
            cls.urls[transport] = f"http://127.0.0.1:{port}/{'sse' if transport == 'sse' else 'mcp'}"
            for _ in range(100):
                if web.started:
                    break
                time.sleep(0.05)

    def _adk(self, transport: str, size: int) -> str:
        async def run() -> str:
            toolset = create_mcp_toolset_http(self.urls[transport], {}, transport=transport, timeout=30)
            try:
                tool = (await toolset.get_tools())[0]
                return str(await tool.run_async(args={"size": size}, tool_context=MagicMock(tool_confirmation=None)))
            finally:
                await toolset.close()
        return asyncio.run(run())

    def _langgraph(self, transport: str, size: int) -> str:
        async def run() -> str:
            tools = await mcp_client.create_mcp_tools(
                {"mcpServers": {"s": {"url": self.urls[transport], "transport": transport, "timeout": 30}}}, "a1")
            call = {"type": "tool_call", "id": "1", "name": "sized", "args": {"size": size}}
            return str((await tools[0].ainvoke(call)).content)
        return asyncio.run(run())

    def test_over_the_sdks_limit(self) -> None:
        for transport in self.urls:
            self.assertGreater(len(self._adk(transport, LARGE)), LARGE, transport)
            self.assertGreater(len(self._langgraph(transport, LARGE)), LARGE, transport)

    def test_over_mates_limit(self) -> None:
        for transport in self.urls:
            self.assertIn("error", self._adk(transport, TOO_LARGE)[:40], transport)
            with self.assertRaises(Exception, msg=transport):
                self._langgraph(transport, TOO_LARGE)


if __name__ == "__main__":
    unittest.main()
