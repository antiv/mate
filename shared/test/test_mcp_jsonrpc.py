"""
What MATE's own MCP servers answer to each kind of JSON-RPC message (mcp_jsonrpc.py),
as found with the MCP SDK's client (#389): the same on the agent, Image Generation and
Google Drive servers.
"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.utils.mcp import google_drive_mcp_server, image_mcp_server
from shared.utils.mcp.agent_mcp_server import AgentMCPServer
from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer
from shared.utils.mcp.image_mcp_server import ImageMCPServer
from shared.utils.mcp.mcp_auth import require_mcp_user
from shared.utils.tools.agent_mcp_protocol_handler import AgentMCPProtocolHandler, a2a_task_failed

PATHS = ("/agents/helper/mcp", "/images/mcp", "/gdrive/mcp")


class TestMcpMessages(unittest.TestCase):

    def setUp(self):
        app = FastAPI()
        for module in ("agent_allowed", "mcp_workspace"):
            patcher = patch(f"shared.utils.mcp.agent_mcp_server.{module}", return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        AgentMCPServer(app, "helper", "Helps")
        ImageMCPServer(app)
        GoogleDriveMCPServer(app)
        for dependency in (require_mcp_user, image_mcp_server._require_user,
                           google_drive_mcp_server.require_drive_user):
            app.dependency_overrides[dependency] = lambda: "caller"
        self.client = TestClient(app, raise_server_exceptions=False)

    def _each(self, body=None, **kwargs):
        for path in PATHS:
            with self.subTest(path=path):
                yield self.client.post(path, **(kwargs or {"json": body}))

    def test_ping_gets_an_empty_result(self):
        for response in self._each({"jsonrpc": "2.0", "id": "p-1", "method": "ping"}):
            self.assertEqual((response.status_code, response.json()),
                             (200, {"jsonrpc": "2.0", "id": "p-1", "result": {}}))

    def test_a_notification_takes_no_answer(self):
        for method in ("notifications/initialized", "notifications/cancelled", "notifications/unknown"):
            for response in self._each({"jsonrpc": "2.0", "method": method}):
                self.assertEqual((response.status_code, response.content), (202, b""))

    def test_a_response_of_the_client_takes_no_answer(self):
        for body in ({"jsonrpc": "2.0", "id": 7, "result": {}},
                     {"jsonrpc": "2.0", "id": 7, "error": {"code": -1, "message": "m"}}):
            for response in self._each(body):
                self.assertEqual((response.status_code, response.content), (202, b""))

    def test_a_body_that_is_not_one_message_is_refused(self):
        for response in self._each(content=b"{"):
            self.assertEqual((response.status_code, response.json()["id"], response.json()["error"]["code"]),
                             (400, None, -32700))
        for response in self._each(content=b"\xff\xfe"):
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, -32700))
        for body in ([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], "ping", 1):
            for response in self._each(body):
                self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, -32600))
        for response in self._each({"jsonrpc": "2.0", "id": 4}):
            self.assertEqual((response.status_code, response.json()["id"], response.json()["error"]["code"]),
                             (400, 4, -32600))

    def test_params_and_arguments_may_be_null(self):
        for body in ({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": None},):
            for response in self._each(body):
                self.assertTrue(response.json()["result"]["tools"])
        for params in (None, {"name": "nope", "arguments": None}):
            for response in self._each({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params}):
                self.assertNotIn("NoneType", response.text)
                self.assertEqual(response.json()["id"], 3)
                self.assertNotEqual(response.json()["error"]["code"], -32603)
        for response in self._each({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": [1]}):
            self.assertEqual(response.json()["error"]["code"], -32602)

    def test_an_unknown_method_and_a_request_without_an_id(self):
        for response in self._each({"jsonrpc": "2.0", "id": 9, "method": "nope"}):
            self.assertEqual((response.status_code, response.json()["id"], response.json()["error"]["code"]),
                             (200, 9, -32601))
        # Not a notification by its name: answered with id 1, as before
        for response in self._each({"jsonrpc": "2.0", "method": "tools/list"}):
            self.assertEqual(response.json()["id"], 1)

    def test_asking_for_a_stream_is_a_405(self):
        for path in PATHS:
            with self.subTest(path=path):
                stream = self.client.get(path, headers={"Accept": "text/event-stream"})
                self.assertEqual((stream.status_code, stream.headers["allow"]), (405, "POST"))
                # A browser, curl, and a client that takes JSON get the information
                for accept in ("text/html,*/*", "*/*", "application/json, text/event-stream"):
                    self.assertEqual(self.client.get(path, headers={"Accept": accept}).status_code, 200)

    def test_the_agent_server_has_no_resource_templates(self):
        response = self.client.post(PATHS[0], json={"jsonrpc": "2.0", "id": 1, "method": "resources/templates/list"})
        self.assertEqual(response.json()["result"], {"resourceTemplates": []})

    def test_an_error_in_a_method_is_answered(self):
        with patch.object(AgentMCPProtocolHandler, "call_mcp_tool", AsyncMock(side_effect=RuntimeError("boom"))):
            response = self.client.post(PATHS[0], json={"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {}})
        self.assertEqual((response.json()["id"], response.json()["error"]["code"]), (5, -32603))


class TestFailedRun(unittest.TestCase):

    def test_which_answers_are_failures(self):
        task = lambda state: {"result": {"kind": "task", "status": {"state": state}}}
        self.assertTrue(a2a_task_failed(task("failed")))
        self.assertTrue(a2a_task_failed(task("rejected")))
        # input-required is a run waiting for an approval
        for answer in (task("completed"), task("input-required"), {"result": {"kind": "message"}},
                       {"result": None}, {"result": {"status": "failed"}}, {}, None, []):
            self.assertFalse(a2a_task_failed(answer), answer)

    def _call(self, state: str) -> dict:
        import asyncio
        import httpx
        answer = {"result": {"kind": "task", "status": {"state": state, "message": {"parts": [{"text": "why"}]}}}}
        response = httpx.Response(200, json=answer, request=httpx.Request("POST", "http://a2a"))
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
            return asyncio.run(AgentMCPProtocolHandler("helper").call_mcp_tool(
                "call_helper_agent", {"message": "hi"}, 2))["result"]

    def test_a_failed_run_is_an_error_of_the_tool(self):
        failed, completed = self._call("failed"), self._call("completed")
        self.assertIs(failed["isError"], True)
        self.assertEqual(failed["content"], [{"type": "text", "text": "why"}])
        self.assertIs(completed["isError"], False)


if __name__ == "__main__":
    unittest.main()
