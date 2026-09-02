#!/usr/bin/env python3
"""
End-to-end check that a per-agent endpoint produces a real HTTP call.

The unit tests assert that an agent's configuration reaches create_model. They
cannot show that the resulting model actually talks to the configured host with
the configured credential, which is the whole claim of the feature — so this
stands up an OpenAI-compatible server on localhost and points an agent at it.

Skips itself if it cannot bind a port or litellm is unavailable, so it does not
turn a sandbox restriction into a red build.
"""

import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.utils import create_model_from_agent_config

RECEIVED = {}


class StubOpenAIHandler(BaseHTTPRequestHandler):
    """The smallest thing that answers POST /v1/chat/completions."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        RECEIVED["path"] = self.path
        RECEIVED["authorization"] = self.headers.get("Authorization")
        RECEIVED["body"] = json.loads(self.rfile.read(length) or b"{}")

        if RECEIVED["body"].get("stream"):
            return self._respond_streaming()

        payload = json.dumps({
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": 0,
            "model": "my-agent",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "answered by the external agent"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _respond_streaming(self):
        """The SSE shape an OpenAI-compatible server uses for stream=True."""
        chunks = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "answered "}}]},
            {"choices": [{"index": 0, "delta": {"content": "by the external agent"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            chunk.update({"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                          "created": 0, "model": "my-agent"})
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass  # keep the test output readable


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestExternalAgentReachesItsEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            cls.port = free_port()
            cls.server = HTTPServer(("127.0.0.1", cls.port), StubOpenAIHandler)
        except OSError as exc:
            raise unittest.SkipTest(f"cannot bind a local port: {exc}")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RECEIVED.clear()

    def _call(self, config):
        try:
            import litellm
        except ImportError:
            self.skipTest("litellm is not installed")
        model = create_model_from_agent_config(config)
        # The model object carries exactly the kwargs MATE built for it; using them
        # is what makes this a test of MATE's configuration rather than of litellm.
        return litellm.completion(
            model=model.model,
            messages=[{"role": "user", "content": "hello"}],
            **model._additional_args,
        )

    def test_the_request_goes_to_the_configured_host_with_the_configured_key(self):
        response = self._call({
            "name": "external", "model_name": "openai/my-agent",
            "model_base_url": f"http://127.0.0.1:{self.port}/v1",
            "model_api_key": "sk-configured",
        })
        self.assertEqual(RECEIVED["path"], "/v1/chat/completions")
        self.assertEqual(RECEIVED["authorization"], "Bearer sk-configured")
        self.assertEqual(RECEIVED["body"]["model"], "my-agent")
        self.assertEqual(response.choices[0].message.content,
                         "answered by the external agent")

    def test_usage_comes_back_so_cost_tracking_has_real_numbers(self):
        # The reason external_openai is the primary adapter: MCP reports no usage.
        response = self._call({
            "name": "external", "model_name": "openai/my-agent",
            "model_base_url": f"http://127.0.0.1:{self.port}/v1",
            "model_api_key": "sk-configured",
        })
        self.assertEqual(response.usage.prompt_tokens, 11)
        self.assertEqual(response.usage.completion_tokens, 7)

    def test_a_key_from_the_environment_reaches_the_wire(self):
        os.environ["EXTERNAL_AGENT_KEY_FOR_TEST"] = "sk-from-env"
        try:
            self._call({
                "name": "external", "model_name": "openai/my-agent",
                "model_base_url": f"http://127.0.0.1:{self.port}/v1",
                "model_api_key": "${EXTERNAL_AGENT_KEY_FOR_TEST}",
            })
        finally:
            os.environ.pop("EXTERNAL_AGENT_KEY_FOR_TEST", None)
        self.assertEqual(RECEIVED["authorization"], "Bearer sk-from-env")

    def test_a_keyless_endpoint_does_not_send_the_provider_key(self):
        # The leak this guards: OPENAI_API_KEY travelling to a third-party host.
        os.environ["OPENAI_API_KEY"] = "sk-must-not-leak"
        try:
            self._call({
                "name": "external", "model_name": "openai/my-agent",
                "model_base_url": f"http://127.0.0.1:{self.port}/v1",
            })
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
        self.assertIsNotNone(RECEIVED["authorization"])
        self.assertNotIn("sk-must-not-leak", RECEIVED["authorization"])

    def test_streaming_works_without_any_translation_layer(self):
        # The plan budgeted work for translating SSE from an external endpoint.
        # litellm already does it, so this asserts the deltas arrive rather than
        # implementing a hop that is not needed.
        try:
            import litellm
        except ImportError:
            self.skipTest("litellm is not installed")
        model = create_model_from_agent_config({
            "name": "external", "model_name": "openai/my-agent",
            "model_base_url": f"http://127.0.0.1:{self.port}/v1",
            "model_api_key": "sk-configured",
        })
        stream = litellm.completion(
            model=model.model,
            messages=[{"role": "user", "content": "hello"}],
            stream=True,
            **model._additional_args,
        )
        text = "".join(
            (chunk.choices[0].delta.content or "") for chunk in stream)
        self.assertEqual(RECEIVED["body"]["stream"], True)
        self.assertEqual(text, "answered by the external agent")


if __name__ == "__main__":
    unittest.main()
