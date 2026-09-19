#!/usr/bin/env python3
"""
End-to-end check that a caller's own tools survive the round trip.

The unit tests cover the translation in isolation. They cannot show that a tool
declared by an OpenCode-style client actually reaches the runtime, that the
model's call comes back as OpenAI `tool_calls`, or that returning the result
resumes the paused turn — which is the whole claim of the feature. So this
stands up a stub /run_sse on localhost and drives the real router against it.

Skips itself if it cannot bind a port, so a sandbox restriction is not a red
build.
"""

import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.openai_routes as openai_routes
from server.pat_auth import get_pat_user
from shared.utils.rate_limit_service import RateLimitResult

RECEIVED = {"run_sse": [], "sessions": []}
SCRIPT = {"turns": []}


def _sse(events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


class StubRuntimeHandler(BaseHTTPRequestHandler):
    """The smallest thing that answers the ADK session + /run_sse contract."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        # First look-up misses so the bridge creates the session, as ADK does.
        RECEIVED["sessions"].append(("GET", self.path))
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        if not self.path.endswith("/run_sse"):
            RECEIVED["sessions"].append(("POST", self.path))
            payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        RECEIVED["run_sse"].append(body)
        index = min(len(RECEIVED["run_sse"]) - 1, len(SCRIPT["turns"]) - 1)
        payload = _sse(SCRIPT["turns"][index])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]},
    },
}


class StubRuntimeCase(unittest.TestCase):
    """Drives the real router against the stub /run_sse on localhost."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        try:
            cls.server = HTTPServer(("127.0.0.1", cls.port), StubRuntimeHandler)
        except OSError as exc:
            raise unittest.SkipTest(f"cannot bind a local port: {exc}")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RECEIVED["run_sse"] = []
        RECEIVED["sessions"] = []
        SCRIPT["turns"] = []

        self._host = patch.object(openai_routes, "ADK_HOST", "127.0.0.1")
        self._port = patch.object(openai_routes, "ADK_PORT", self.port)
        self._host.start()
        self._port.start()
        self.addCleanup(self._host.stop)
        self.addCleanup(self._port.stop)

        agent = MagicMock()
        agent.disabled = False
        agent.expose_as_model = True
        agent.project_id = 7
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = agent
        # Usage lookup: no rows recorded by the stub runtime.
        session.query.return_value.filter.return_value.first.return_value = (None, None)
        db = MagicMock()
        db.get_session.return_value = session
        self._db = patch.object(openai_routes, "get_database_client", return_value=db)
        self._db.start()
        self.addCleanup(self._db.stop)

        app = FastAPI()
        app.include_router(openai_routes.router)
        user = MagicMock()
        user.user_id = "tester"
        app.dependency_overrides[get_pat_user] = lambda: user
        self.client = TestClient(app)

    def _post(self, messages, tools=None, stream=False):
        payload = {"model": "coder", "messages": messages, "stream": stream}
        if tools is not None:
            payload["tools"] = tools
        return self.client.post("/v1/chat/completions", json=payload)


class TestClientToolRoundTrip(StubRuntimeCase):

    def test_the_caller_tools_reach_the_runtime(self):
        SCRIPT["turns"] = [[{
            "author": "coder", "invocationId": "e-1",
            "content": {"role": "model", "parts": [{"text": "on it"}]},
        }]]
        response = self._post([{"role": "user", "content": "hi"}], tools=[READ_TOOL])
        self.assertEqual(response.status_code, 200)

        metadata = RECEIVED["run_sse"][0]["custom_metadata"]
        declarations = metadata[openai_routes.CLIENT_TOOL_METADATA_KEY]
        self.assertEqual([d["name"] for d in declarations], ["read"])

    def test_a_request_without_tools_declares_none(self):
        SCRIPT["turns"] = [[{
            "author": "coder", "invocationId": "e-1",
            "content": {"role": "model", "parts": [{"text": "hello"}]},
        }]]
        response = self._post([{"role": "user", "content": "hi"}])
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("custom_metadata", RECEIVED["run_sse"][0])
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "hello")

    def test_a_call_to_a_caller_tool_comes_back_as_tool_calls(self):
        SCRIPT["turns"] = [[
            {"author": "coder", "invocationId": "e-1",
             "content": {"role": "model", "parts": [{"text": "let me look"}]}},
            {"author": "coder", "invocationId": "e-1",
             "content": {"role": "model", "parts": [
                 {"functionCall": {"id": "adk-1", "name": "read", "args": {"path": "a.py"}}}]},
             "longRunningToolIds": ["adk-1"]},
        ]]
        response = self._post([{"role": "user", "content": "read a.py"}], tools=[READ_TOOL])
        body = response.json()
        choice = body["choices"][0]

        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(len(choice["message"]["tool_calls"]), 1)
        call = choice["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "adk-1")
        self.assertEqual(call["function"]["name"], "read")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"path": "a.py"})

    def test_returning_the_result_resumes_the_turn(self):
        # The tool result is a single runtime turn: the paused invocation resumes
        # and finishes with an answer.
        SCRIPT["turns"] = [
            [{"author": "coder", "invocationId": "e-2",
              "content": {"role": "model", "parts": [{"text": "the file imports os"}]}}],
        ]
        messages = [
            {"role": "user", "content": "read a.py"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "adk-1", "type": "function",
                             "function": {"name": "read", "arguments": '{"path":"a.py"}'}}]},
            {"role": "tool", "tool_call_id": "adk-1", "name": "read", "content": "import os"},
        ]
        response = self._post(messages, tools=[READ_TOOL])
        body = response.json()

        # Only the tool result is sent; the history the runtime already holds is not replayed.
        sent = RECEIVED["run_sse"][-1]["new_message"]
        self.assertEqual(sent["parts"], [{
            "function_response": {"id": "adk-1", "name": "read",
                                  "response": {"result": "import os"}},
        }])
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["choices"][0]["message"]["content"], "the file imports os")

    def test_the_agents_own_tool_calls_are_not_offered_to_the_caller(self):
        # The caller cannot run google_search; showing it as work it owes would
        # deadlock the conversation.
        SCRIPT["turns"] = [[
            {"author": "coder", "invocationId": "e-1",
             "content": {"role": "model", "parts": [
                 {"functionCall": {"id": "adk-9", "name": "google_search", "args": {"q": "x"}}}]}},
            {"author": "coder", "invocationId": "e-1",
             "content": {"role": "model", "parts": [{"text": "found it"}]}},
        ]]
        response = self._post([{"role": "user", "content": "search"}], tools=[READ_TOOL])
        choice = response.json()["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertNotIn("tool_calls", choice["message"])
        self.assertEqual(choice["message"]["content"], "found it")

    def test_streaming_emits_a_tool_call_delta_and_a_done_sentinel(self):
        SCRIPT["turns"] = [[
            {"author": "coder", "invocationId": "e-1",
             "content": {"role": "model", "parts": [
                 {"functionCall": {"id": "adk-1", "name": "read", "args": {"path": "a.py"}}}]}},
        ]]
        response = self._post([{"role": "user", "content": "read"}],
                              tools=[READ_TOOL], stream=True)
        frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(frames[-1], "[DONE]")

        chunks = [json.loads(f) for f in frames[:-1]]
        tool_deltas = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        self.assertEqual(len(tool_deltas), 1)
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_an_assistant_message_with_null_content_is_accepted(self):
        # The client replays its own history; refusing null content rejected the
        # whole request with a 422 before this existed.
        SCRIPT["turns"] = [[{
            "author": "coder", "invocationId": "e-1",
            "content": {"role": "model", "parts": [{"text": "ok"}]},
        }]]
        response = self._post([
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": []},
            {"role": "user", "content": "again"},
        ])
        self.assertEqual(response.status_code, 200)

    def test_a_turn_with_nothing_new_is_refused(self):
        response = self._post([{"role": "user", "content": "go"},
                               {"role": "assistant", "content": "done"}])
        self.assertEqual(response.status_code, 400)

    def test_an_unexposed_agent_is_not_reachable(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        db = MagicMock()
        db.get_session.return_value = session
        with patch.object(openai_routes, "get_database_client", return_value=db):
            response = self._post([{"role": "user", "content": "hi"}])
        self.assertEqual(response.status_code, 404)


class TestRateLimiting(StubRuntimeCase):
    """
    The bridge dials the runtime itself, so the RateLimitMiddleware never sees
    it; the router has to consult the service on its own.
    """

    def setUp(self):
        super().setUp()
        self._env = patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "true"})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.svc = MagicMock()
        self.svc.check_request_limit = AsyncMock(
            return_value=(RateLimitResult(allowed=True, action="warn", message="ok"), None))
        self.svc.record_request = AsyncMock()
        self._svc = patch.object(openai_routes, "get_rate_limit_service", return_value=self.svc)
        self._svc.start()
        self.addCleanup(self._svc.stop)

    def test_a_blocked_request_is_a_429_with_retry_after(self):
        self.svc.check_request_limit.return_value = (
            RateLimitResult(allowed=False, action="block", message="Too many",
                            retry_after_seconds=42.7),
            None,
        )
        response = self._post([{"role": "user", "content": "hi"}])
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "42")
        self.assertEqual(response.json()["detail"], "Too many")
        self.assertEqual(RECEIVED["run_sse"], [])
        self.svc.record_request.assert_not_called()

    def test_the_limit_is_keyed_on_the_pat_user_and_the_agents_project(self):
        SCRIPT["turns"] = [[{
            "author": "coder", "invocationId": "e-1",
            "content": {"role": "model", "parts": [{"text": "hello"}]},
        }]]
        response = self._post([{"role": "user", "content": "hi"}])
        self.assertEqual(response.status_code, 200)
        self.svc.check_request_limit.assert_awaited_once_with(
            user_id="tester", agent_name="coder", project_id=7, auth_username="tester")
        self.svc.record_request.assert_awaited_once_with(user_id="tester", agent_name="coder")

    def test_a_request_that_fans_out_into_two_runtime_turns_counts_once(self):
        SCRIPT["turns"] = [
            [{"author": "coder", "invocationId": "e-2",
              "content": {"role": "model", "parts": [{"text": "imports os"}]}}],
            [{"author": "coder", "invocationId": "e-3",
              "content": {"role": "model", "parts": [{"text": "and sys"}]}}],
        ]
        messages = [
            {"role": "user", "content": "read a.py"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "adk-1", "type": "function",
                             "function": {"name": "read", "arguments": '{"path":"a.py"}'}}]},
            {"role": "tool", "tool_call_id": "adk-1", "name": "read", "content": "import os"},
            {"role": "user", "content": "anything else?"},
        ]
        response = self._post(messages, tools=[READ_TOOL])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(RECEIVED["run_sse"]), 2)
        self.svc.check_request_limit.assert_awaited_once()
        self.svc.record_request.assert_awaited_once()

    def test_a_disabled_limiter_is_not_consulted(self):
        SCRIPT["turns"] = [[{
            "author": "coder", "invocationId": "e-1",
            "content": {"role": "model", "parts": [{"text": "hello"}]},
        }]]
        with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}):
            response = self._post([{"role": "user", "content": "hi"}])
        self.assertEqual(response.status_code, 200)
        self.svc.check_request_limit.assert_not_called()


class TestStreamingIsIncremental(unittest.TestCase):
    """
    The bridge must hand each delta on as it arrives. Collecting the whole turn
    first and flushing at the end looks identical in the final payload, and
    leaves the caller staring at silence for the length of an agent turn.
    """

    def test_a_delta_is_yielded_before_the_upstream_stream_ends(self):
        import asyncio

        from server.openai_translate import TextDeltaTracker

        produced = []

        def frame(text):
            return json.dumps({
                "author": "coder", "invocationId": "e-1",
                "content": {"role": "model", "parts": [{"text": text}]},
            })

        frames = [f"data: {frame('one')}\n\n", f"data: {frame('one two')}\n\n",
                  f"data: {frame('one two three')}\n\n"]

        class FakeResponse:
            status_code = 200

            async def aiter_bytes(self):
                for chunk in frames:
                    produced.append(chunk)
                    yield chunk.encode()

        class FakeStream:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *exc):
                return False

        class FakeClient:
            def stream(self, *args, **kwargs):
                return FakeStream()

        async def first_item():
            agen = openai_routes._stream_turn(
                FakeClient(), {}, set(), TextDeltaTracker(), {})
            item = await agen.__anext__()
            still_pending = len(produced) < len(frames)
            await agen.aclose()
            return item, still_pending

        item, still_pending = asyncio.run(first_item())
        self.assertEqual(item, ("text", "one"))
        self.assertTrue(still_pending,
                        "the first delta was only produced after the whole stream was read")


if __name__ == "__main__":
    unittest.main()
