#!/usr/bin/env python3
"""
MATE's own MCP servers (agents, Image Generation, Google Drive): what they answer to each
kind of message, with the SDK's client over HTTP and without one; and that an agent's tool
runs the agent as the caller.
"""

import asyncio
import base64
import json
import os
import socket
import sys
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import httpx2
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.utils.agent_invoke import ReplyCollector
from shared.utils.mcp import agent_mcp_server, image_mcp_server
from shared.utils.mcp.agent_mcp_server import AgentMCPServer, mcp_session_id
from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer
from shared.utils.mcp.image_mcp_server import ImageMCPServer
from shared.utils.mcp.mcp_auth import require_drive_user, require_mcp_user
from shared.utils.response_metrics import resolve_origin

PATHS = ("/agents/helper/mcp", "/images/mcp", "/gdrive/mcp")
RUN = "shared.utils.mcp.agent_mcp_server.run_agent"


def _run(reply: str = "", error: str = "", waiting_for: Optional[List[str]] = None) -> ReplyCollector:
    run = ReplyCollector()
    run._text, run.error, run.waiting_for = reply, error, waiting_for or []
    return run


class _Servers(unittest.TestCase):
    """The three servers on one app. The caller is the x-caller header."""

    def setUp(self) -> None:
        app = FastAPI()
        for name in ("agent_allowed", "mcp_workspace"):
            patcher = patch(f"shared.utils.mcp.agent_mcp_server.{name}", return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for agent in ("helper", "other"):
            AgentMCPServer(app, agent, "Helps with things.")
        self.images = ImageMCPServer(app)
        self.drive = GoogleDriveMCPServer(app)

        def caller(request: Request) -> str:
            return request.headers.get("x-caller", "alice")
        for dependency in (require_mcp_user, image_mcp_server._require_user, require_drive_user):
            app.dependency_overrides[dependency] = caller
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)
        limits = patch.object(AgentMCPServer, "_within_limits", AsyncMock())
        self.limits = limits.start()
        self.addCleanup(limits.stop)

    def post(self, path: str, method: str, params: Any = None, **headers: str) -> Any:
        body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}
        return self.client.post(path, json=body, headers=headers).json()

    def call(self, path: str, tool: str, arguments: Dict[str, Any], **headers: str) -> Any:
        return self.post(path, "tools/call", {"name": tool, "arguments": arguments}, **headers)


class TestMessages(_Servers):

    def test_ping_tools_and_an_unknown_method(self) -> None:
        for path in PATHS:
            with self.subTest(path):
                self.assertEqual(self.post(path, "ping"), {"jsonrpc": "2.0", "id": 7, "result": {}})
                self.assertTrue(self.post(path, "tools/list")["result"]["tools"])
                self.assertEqual(self.post(path, "nope")["error"]["code"], -32601)

    def test_notifications_and_responses_take_no_answer(self) -> None:
        for path in PATHS:
            for body in ({"jsonrpc": "2.0", "method": "notifications/initialized"},
                         {"jsonrpc": "2.0", "id": 7, "result": {}}):
                response = self.client.post(path, json=body)
                self.assertEqual((response.status_code, response.content), (202, b""), path)

    def test_a_body_that_is_not_one_message_is_refused(self) -> None:
        for path in PATHS:
            for content in (b"{", b"\xff\xfe", b"[]", b"1", b'{"jsonrpc": "2.0", "id": 4}'):
                response = self.client.post(path, content=content, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 400, (path, content))

    def test_the_protocol_version_is_negotiated(self) -> None:
        for path in PATHS:
            for asked in ("2024-11-05", "2025-06-18"):
                result = self.post(path, "initialize", {"protocolVersion": asked, "capabilities": {},
                                                        "clientInfo": {"name": "t", "version": "1"}})["result"]
                self.assertEqual(result["protocolVersion"], asked)

    def test_a_client_that_names_no_accept_header_is_answered(self) -> None:
        for path in PATHS:
            for accept in ("*/*", "application/json", "text/html"):
                response = self.client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                            headers={"Accept": accept})
                self.assertEqual((response.status_code, response.headers["content-type"]), (200, "application/json"))

    def test_the_routes_of_one_method(self) -> None:
        for path in PATHS:
            for content in (b"", b"{}", b'{"params": null}', b"nonsense"):
                answer = self.client.post(f"{path}/tools/list", content=content).json()
                self.assertEqual(answer["id"], 1)
                self.assertTrue(answer["result"]["tools"])
                self.assertIn("protocolVersion", self.client.post(f"{path}/initialize", content=content).json()["result"])
            # The method is the route's, whatever the body says
            answer = self.client.post(f"{path}/tools/list", json={"id": "a", "method": "tools/call"}).json()
            self.assertEqual((answer["id"], "tools" in answer["result"]), ("a", True))

    def test_a_get_is_for_people(self) -> None:
        for path in PATHS:
            stream = self.client.get(path, headers={"Accept": "text/event-stream"})
            self.assertEqual((stream.status_code, stream.headers["allow"]), (405, "POST"))
            for accept in ("text/html,*/*", "*/*", "application/json, text/event-stream"):
                self.assertIn("available_tools", self.client.get(path, headers={"Accept": accept}).json())
            self.assertEqual(self.client.get(f"{path}/sse").status_code, 404)

    def test_wrong_arguments(self) -> None:
        for path, tool, arguments in (
                (PATHS[0], "call_helper_agent", {}), (PATHS[0], "call_helper_agent", {"message": ""}),
                (PATHS[0], "call_helper_agent", {"message": "hi", "session_id": 1}), (PATHS[0], "call_other_agent", {}),
                (PATHS[1], "generate_image_dall_e_3", {"prompt": "x", "n": 50}), (PATHS[1], "nope", {}),
                (PATHS[2], "read_google_doc", {}), (PATHS[2], "get_google_drive_service", {})):
            with patch(RUN) as run:
                answer = self.call(path, tool, arguments)
            self.assertEqual(answer["error"]["code"], -32602, (tool, arguments))
            run.assert_not_called()

    def test_a_server_that_is_not_available(self) -> None:
        self.images.image_mcp_available = self.drive.gdrive_mcp_available = False
        for path in PATHS[1:]:
            self.assertEqual(self.client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code, 503)
            self.assertEqual(self.client.post(f"{path}/tools/list").status_code, 503)
            self.assertEqual(self.client.get(path).status_code, 503)
            self.assertEqual(self.client.get(f"{path}/health").json()["status"], "unhealthy")


class TestAgentTool(_Servers):

    def agent(self, run: Any = None, caller: str = "alice", agent: str = "helper", path: str = "",
              **arguments: Any) -> Any:
        with patch(RUN, AsyncMock(return_value=run or _run("hello"))) as self.run_agent:
            return self.call(f"/agents/{agent}/mcp{path}", f"call_{agent}_agent", {"message": "hi", **arguments},
                             **{"x-caller": caller})["result"]

    def session(self, **kwargs: Any) -> str:
        self.agent(**kwargs)
        return self.run_agent.call_args.args[2]

    def test_it_runs_as_the_caller(self) -> None:
        result = self.agent(caller="bob")
        self.assertEqual((result["content"], result["isError"]), ([{"type": "text", "text": "hello"}], False))
        name, user, session, message = self.run_agent.call_args.args[:4]
        self.assertEqual((name, user, message), ("helper", "bob", "hi"))
        # An approval it asks for is MCP's, so the caller can't answer it
        self.assertEqual(resolve_origin(user, session), "mcp")
        self.limits.assert_awaited_once_with("bob")

    def test_a_session_is_the_callers(self) -> None:
        self.assertEqual(self.session(session_id="s1"), mcp_session_id("alice", "helper", "s1"))
        self.assertEqual(self.session(session_id="s1", path="/tools/call"), mcp_session_id("alice", "helper", "s1"))
        others = {self.session(session_id="s1", caller="bob"), self.session(session_id="s1", agent="other"),
                  self.session(session_id="s2"), self.session(session_id=mcp_session_id("alice", "helper", "s1"))}
        self.assertEqual(len(others | {mcp_session_id("alice", "helper", "s1")}), 5)
        # The parts can't be shifted into each other
        self.assertNotEqual(mcp_session_id("a", "b", "c"), mcp_session_id("a", "b\", \"c", ""))

    def test_without_a_session_each_call_is_its_own(self) -> None:
        self.assertEqual(len({self.session(), self.session(), self.session(session_id="")}), 3)

    def test_a_failed_run_is_an_error_of_the_tool(self) -> None:
        result = self.agent(_run(error="no key"))
        self.assertEqual((result["isError"], result["content"][0]["text"]), (True, "no key"))
        for error in (RuntimeError("run_sse returned HTTP 500"), httpx.ConnectError("down"), httpx.ReadTimeout("slow")):
            with patch(RUN, AsyncMock(side_effect=error)):
                self.assertTrue(self.call(PATHS[0], "call_helper_agent", {"message": "hi"})["result"]["isError"])

    def test_a_run_that_waits_for_approval_says_so(self) -> None:
        result = self.agent(_run(waiting_for=["refund"]))
        self.assertFalse(result["isError"])
        self.assertIn("approval to use refund", result["content"][0]["text"])

    def test_over_a_limit_it_doesnt_run(self) -> None:
        self.limits.side_effect = MCPError(-32000, "Budget used up")
        with patch(RUN) as run:
            answer = self.call(PATHS[0], "call_helper_agent", {"message": "hi"})
        self.assertEqual(answer["error"]["message"], "Budget used up")
        run.assert_not_called()

    def test_what_the_run_stream_says(self) -> None:
        def read(*events: Dict[str, Any]) -> ReplyCollector:
            collector = ReplyCollector()
            for event in events:
                collector.feed(f"data: {json.dumps(event)}\n\n".encode())
            return collector
        self.assertEqual(read({"error": "boom"}).error, "boom")
        self.assertEqual(read({"author": "a", "errorMessage": "no"}).error, "no")
        asked = {"functionCall": {"name": "adk_request_confirmation", "args": {"originalFunctionCall": {"name": "refund"}}}}
        self.assertEqual(read({"author": "a", "content": {"parts": [asked]}}).waiting_for, ["refund"])
        plain = read({"author": "a", "content": {"parts": [{"functionCall": {"name": "look_up"}}]}},
                     {"author": "a", "content": {"parts": [{"text": "done"}]}})
        self.assertEqual((plain.reply, plain.error, plain.waiting_for), ("done", "", []))


class TestLimits(unittest.TestCase):

    def test_the_callers_and_the_agents_limits_are_asked(self) -> None:
        server = AgentMCPServer(FastAPI(), "helper", "Helps")
        refused = HTTPException(status_code=429, detail="Too many requests")
        with patch("shared.utils.database_client.get_database_client") as db, \
                patch("server.openai_routes._enforce_rate_limit", AsyncMock(side_effect=refused)) as limit:
            db.return_value.get_session.return_value.query.return_value.filter_by.return_value.first.return_value = (5,)
            with self.assertRaises(MCPError) as raised:
                asyncio.run(server._within_limits("bob"))
        limit.assert_awaited_once_with("bob", "helper", 5)
        self.assertEqual(raised.exception.message, "Too many requests")


class TestImagesAndDrive(_Servers):

    def test_an_image_comes_back_as_one(self) -> None:
        async def generate(prompt: str, context: Any, model: str, config: Dict[str, Any]) -> Dict[str, Any]:
            from google.genai import types as genai
            await context.save_artifact("a.png", genai.Part.from_bytes(data=b"\x89PNG", mime_type="image/png"))
            self.asked = (prompt, model, config)
            return {"success": True}
        with patch("shared.utils.tools.image_tools._generate_image_internal", generate):
            result = self.call(PATHS[1], "generate_image_dall_e_3", {"prompt": "a lamp", "quality": "hd"})["result"]
        self.assertEqual(self.asked, ("a lamp", "dall-e-3", {"size": "1024x1024", "quality": "hd", "n": 1}))
        self.assertEqual(result["content"][1], {"type": "image", "mimeType": "image/png",
                                                "data": base64.b64encode(b"\x89PNG").decode()})

    def test_a_failed_image_is_an_error(self) -> None:
        with patch("shared.utils.tools.image_tools._generate_image_internal",
                   AsyncMock(return_value={"success": False, "error": "no key"})):
            result = self.call(PATHS[1], "generate_image_nano_banana", {"prompt": "x"})["result"]
        self.assertEqual((result["isError"], result["content"][0]["text"]), (True, "no key"))

    def test_drive_passes_the_arguments_in_order(self) -> None:
        for tool, arguments, given in (
                ("list_files_in_folder", {}, ("",)),
                ("read_google_doc_by_name", {"folder_id": "f", "doc_name": "d"}, ("d", "f")),
                ("find_by_name", {"name": "n"}, ("n", "")),
                ("get_file_metadata", {"file_id": "x"}, ("x",))):
            with patch(f"shared.utils.tools.google_drive_tools.{tool}",
                       return_value={"status": "success", "message": "ok"}) as function:
                result = self.call(PATHS[2], tool, arguments)["result"]
            self.assertEqual(function.call_args.args, (*given, None))
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["message"], "ok")
        with patch("shared.utils.tools.google_drive_tools.read_google_doc",
                   return_value={"status": "error", "error_message": "gone"}):
            self.assertTrue(self.call(PATHS[2], "read_google_doc", {"doc_id": "x"})["result"]["isError"])

    def test_the_arguments_are_the_functions(self) -> None:
        import inspect
        from shared.utils.mcp.google_drive_mcp_server import TOOLS
        from shared.utils.tools import google_drive_tools
        for tool in TOOLS:
            parameters = list(inspect.signature(getattr(google_drive_tools, tool.name)).parameters)
            self.assertEqual(parameters, [*tool.input_schema["properties"], "tool_context"], tool.name)


class TestWithTheSdksClient(_Servers):

    def setUp(self) -> None:
        super().setUp()
        import uvicorn
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        web = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=web.run, daemon=True).start()
        self.addCleanup(setattr, web, "should_exit", True)
        for _ in range(100):
            if web.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{port}"

    def with_client(self, path: str, use: Any) -> Any:
        async def run() -> Any:
            async with httpx2.AsyncClient(headers={"x-caller": "bob"}) as http:
                async with streamable_http_client(self.url + path, http_client=http) as (read, write), \
                        ClientSession(read, write) as session:
                    await session.initialize()
                    await session.send_ping()
                    return await use(session)
        return asyncio.run(run())

    def test_an_agent(self) -> None:
        async def use(session: ClientSession) -> Any:
            prompt = await session.get_prompt("ask_helper", {"question": "Why?"})
            resource = await session.read_resource("mate://agents/helper/about")
            self.assertEqual((await session.list_resource_templates()).resource_templates, [])
            for refused in (session.get_prompt("ask_helper", {}), session.get_prompt("other", {"question": "x"}),
                            session.read_resource("mate://agents/other/about")):
                with self.assertRaises(MCPError):
                    await refused
            return ((await session.list_tools()).tools, (await session.list_prompts()).prompts, prompt,
                    (await session.list_resources()).resources, resource,
                    await session.call_tool("call_helper_agent", {"message": "hi"}))
        with patch(RUN, AsyncMock(return_value=_run("hello"))) as run:
            tools, prompts, prompt, resources, resource, result = self.with_client(PATHS[0], use)
        self.assertEqual([t.name for t in tools], ["call_helper_agent"])
        self.assertTrue(prompts[0].arguments[0].required)
        self.assertIn("call_helper_agent", prompt.messages[0].content.text)
        self.assertTrue(prompt.messages[0].content.text.endswith("Why?"))
        self.assertEqual([str(r.uri) for r in resources], ["mate://agents/helper/about"])
        self.assertIn("Helps with things.", resource.contents[0].text)
        self.assertEqual((result.content[0].text, result.is_error), ("hello", False))
        self.assertEqual(run.call_args.args[1], "bob")

    def test_images_and_drive(self) -> None:
        async def names(session: ClientSession) -> List[types.Tool]:
            return (await session.list_tools()).tools
        self.assertEqual(len(self.with_client(PATHS[1], names)), 3)
        self.assertEqual(len(self.with_client(PATHS[2], names)), 7)

    def test_the_newest_protocol(self) -> None:
        from mcp import Client

        async def run() -> Any:
            async with Client(self.url + PATHS[0]) as client:
                return (await client.list_tools()).tools
        self.assertEqual([t.name for t in asyncio.run(run())], ["call_helper_agent"])

    def test_calls_at_once(self) -> None:
        async def slow(*args: Any) -> ReplyCollector:
            await asyncio.sleep(0.2)
            return _run(args[3])

        async def use(session: ClientSession) -> List[str]:
            results = await asyncio.gather(*(session.call_tool("call_helper_agent", {"message": str(n)})
                                             for n in range(8)))
            return [r.content[0].text for r in results]
        with patch(RUN, slow):
            self.assertEqual(self.with_client(PATHS[0], use), [str(n) for n in range(8)])


if __name__ == "__main__":
    unittest.main()
