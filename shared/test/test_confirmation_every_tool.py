#!/usr/bin/env python3
"""
require_confirmation holds for an MCP server's tools, on both runtimes: a real server, a
real run, and the tool runs only when approved. A named tool that can't ask is left out.
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
import unittest
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from shared.utils.langgraph import mcp_client
from shared.utils.langgraph.hitl import CONFIRMATIONS_KEY, apply_confirmation_wrapping, pending_confirmations
from shared.utils.tools.confirmation import apply_confirmation
from shared.utils.tools.tool_factory import ToolFactory

CONFIRMATION = "adk_request_confirmation"
RAN: List[str] = []


def _serve() -> Any:
    import uvicorn
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:  # mcp 1
        from mcp.server.fastmcp import FastMCP as MCPServer

    server = MCPServer("orders")

    @server.tool()
    def refund(order: str) -> str:
        """Refund an order."""
        RAN.append(f"refund {order}")
        return f"refunded {order}"

    @server.tool()
    def look_up(order: str) -> str:
        """Look up an order."""
        RAN.append(f"look_up {order}")
        return f"order {order}"

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    web = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=web.run, daemon=True).start()
    for _ in range(100):
        if web.started:
            break
        time.sleep(0.05)
    return web, f"http://127.0.0.1:{port}/mcp"


class _Model(BaseLlm):
    """Calls the tool it is told to, then answers."""
    model: str = "scripted"
    tool: str = ""

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncIterator[LlmResponse]:
        last = llm_request.contents[-1].parts[-1]
        if last.function_response:
            part = types.Part(text="done")
        else:
            part = types.Part(function_call=types.FunctionCall(id="c1", name=self.tool, args={"order": "7"}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class TestMcpToolsAskOnAdk(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web, cls.url = _serve()
        cls.addClassCleanup(setattr, cls.web, "should_exit", True)

    def setUp(self) -> None:
        RAN.clear()

    def _run(self, tool: str, answer: Optional[bool] = None) -> List[Any]:
        """The calls the agent asked to be confirmed."""
        async def run() -> List[Any]:
            tools = [t for t in ToolFactory().create_tools({
                "name": "a1", "project_id": None,
                "mcp_servers_config": json.dumps({"mcpServers": {"orders": {"url": self.url}}}),
                "tool_config": json.dumps({"require_confirmation": {"tools": ["refund"], "approver": "chat"}}),
            }) if isinstance(t, BaseToolset)]
            runner = InMemoryRunner(agent=Agent(name="a1", model=_Model(tool=tool), tools=tools), app_name="a1")
            session = await runner.session_service.create_session(app_name="a1", user_id="u")

            async def send(part: types.Part) -> List[Any]:
                calls = []
                async for event in runner.run_async(user_id="u", session_id=session.id,
                                                    new_message=types.Content(role="user", parts=[part])):
                    calls += [c for c in event.get_function_calls() if c.name == CONFIRMATION]
                return calls

            asked = await send(types.Part(text="go"))
            if answer is not None:
                self.assertEqual(RAN, [], "ran before it was answered")
                await send(types.Part(function_response=types.FunctionResponse(
                    id=asked[0].id, name=CONFIRMATION, response={"confirmed": answer})))
            for toolset in tools:
                await toolset.close()
            return asked
        with patch("shared.utils.outbound.outbound_refusal", return_value=None):
            return asyncio.run(run())

    def test_it_waits(self) -> None:
        asked = self._run("refund")
        self.assertEqual([c.args["originalFunctionCall"]["name"] for c in asked], ["refund"])
        self.assertEqual(asked[0].args["originalFunctionCall"]["args"], {"order": "7"})
        self.assertEqual(RAN, [])

    def test_approved(self) -> None:
        self._run("refund", answer=True)
        self.assertEqual(RAN, ["refund 7"])

    def test_rejected(self) -> None:
        self._run("refund", answer=False)
        self.assertEqual(RAN, [])

    def test_a_tool_not_named_runs(self) -> None:
        self.assertEqual(self._run("look_up"), [])
        self.assertEqual(RAN, ["look_up 7"])


class TestMcpToolsAskOnLangGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web, cls.url = _serve()
        cls.addClassCleanup(setattr, cls.web, "should_exit", True)

    def setUp(self) -> None:
        RAN.clear()

    def _run(self, tool: str, answer: Optional[bool] = None) -> Dict[str, Any]:
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.prebuilt import create_react_agent
        from langgraph.types import Command

        class Model(GenericFakeChatModel):
            def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
                return self

        async def run() -> Dict[str, Any]:
            tools = await mcp_client.create_mcp_tools({"mcpServers": {"orders": {"url": self.url}}}, "a1")
            tools = apply_confirmation_wrapping(tools, ["refund"], "a1")
            self.assertEqual({t.name: t.description for t in tools},
                             {"refund": "Refund an order.", "look_up": "Look up an order."})
            self.assertEqual(next(t for t in tools if t.name == "refund").args["order"]["type"], "string")
            model = Model(messages=iter([
                AIMessage(content="", tool_calls=[{"id": "c1", "name": tool, "args": {"order": "7"}}]),
                AIMessage(content="done")]))
            graph = create_react_agent(model, tools=tools, checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": "t"}}
            await graph.ainvoke({"messages": [HumanMessage("go")]}, config)
            state = await graph.aget_state(config)
            pending = pending_confirmations(state)
            if answer is not None:
                self.assertEqual(RAN, [], "ran before it was answered")
                await graph.ainvoke(Command(resume={pending[0]["interrupt_id"]: {
                    CONFIRMATIONS_KEY: {pending[0]["confirmation_id"]: answer}}}), config)
                state = await graph.aget_state(config)
            return {"pending": pending, "messages": state.values["messages"], "tasks": state.tasks}
        with patch("shared.utils.outbound.outbound_refusal", return_value=None):
            return asyncio.run(run())

    def test_it_waits(self) -> None:
        result = self._run("refund")
        self.assertEqual(len(result["pending"]), 1)
        call = result["tasks"][0].interrupts[0].value["originalFunctionCall"]
        self.assertEqual(call, {"id": "c1", "name": "refund", "args": {"order": "7"}})
        self.assertEqual(RAN, [])

    def test_approved(self) -> None:
        result = self._run("refund", answer=True)
        self.assertEqual(RAN, ["refund 7"])
        self.assertIn("refunded 7", str(result["messages"][-2].content))

    def test_rejected(self) -> None:
        result = self._run("refund", answer=False)
        self.assertEqual(RAN, [])
        self.assertIn("rejected", result["messages"][-2].content)
        self.assertEqual(result["messages"][-1].content, "done")

    def test_a_tool_not_named_runs(self) -> None:
        self.assertEqual(self._run("look_up")["pending"], [])
        self.assertEqual(RAN, ["look_up 7"])


class TestAToolThatCannotAsk(unittest.TestCase):
    def test_adk(self) -> None:
        class Other(BaseTool):
            async def run_async(self, *, args: Any, tool_context: Any) -> Any:
                return "ran"

        class Toolset(BaseToolset):
            async def get_tools(self, readonly_context: Any = None) -> List[BaseTool]:
                return [Other(name="inner", description="d"), Other(name="free", description="d")]

            async def close(self) -> None:
                pass

        def plain() -> str:
            return "ran"

        given = FunctionTool(plain)
        with self.assertLogs("shared.utils.tools.confirmation", "ERROR") as logs:
            left = apply_confirmation([Other(name="outer", description="d"), given, Toolset()],
                                      ["outer", "plain", "inner"], "a1")
            inner = asyncio.run(left[-1].get_tools())
        self.assertEqual(len(left), 2)
        self.assertIs(left[0], given)
        self.assertTrue(asyncio.run(given.check_require_confirmation({}, MagicMock())))
        self.assertEqual([t.name for t in inner], ["free"])
        self.assertEqual(len(logs.output), 2)

    def test_langgraph(self) -> None:
        class Other:
            name = "outer"

        with self.assertLogs("shared.utils.langgraph.hitl", "ERROR"):
            self.assertEqual(apply_confirmation_wrapping([Other()], ["outer"], "a1"), [])


if __name__ == "__main__":
    unittest.main()
