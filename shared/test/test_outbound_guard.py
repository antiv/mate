#!/usr/bin/env python3
"""
A guarded model call reaches no internal address, with the HTTP clients themselves.

Real requests to a server on this machine, which is an internal address: of httpx and
httpx2, the OpenAI SDK, and LiteLLM's providers. Each is made unguarded first, so that a
client the server never hears from doesn't pass as guarded. An upgrade that moves a
client to another HTTP library fails here.
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List
from unittest.mock import patch

import httpx
import litellm
from openai import AsyncOpenAI, OpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.callbacks.model_fallback_callback import is_provider_outage
from shared.utils import outbound
from shared.utils.provider_keys import guarded

ANSWER = json.dumps({
    "id": "c", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
MESSAGES = [{"role": "user", "content": "x"}]
# Provider prefix → what its requests add to the base URL given
PROVIDERS = {"openai/m": "/v1", "openrouter/x/m": "/v1", "anthropic/claude-3-5-haiku-20241022": "",
             "mistral/m": "/v1", "groq/m": "/v1", "deepseek/m": "/v1", "xai/m": "/v1",
             "gemini/gemini-2.5-flash": "", "ollama_chat/m": "", "hosted_vllm/m": "/v1"}


class _Server:
    """Answers every request; or, with a target, redirects it there."""

    def __init__(self, target: str = "") -> None:
        self.hits: List[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length", 0)))
                outer.hits.append(self.path)
                self.send_response(307 if target else 200)
                if target:
                    self.send_header("location", target + self.path)
                body = b"" if target else ANSWER
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _calls(url: str) -> Dict[str, Callable[[], Any]]:
    calls: Dict[str, Callable[[], Any]] = {}
    for library in outbound.HTTP_LIBRARIES:
        def asynchronous(library: Any = library) -> Any:
            async def post() -> Any:
                async with library.AsyncClient(follow_redirects=True) as client:
                    return await client.post(url)
            return asyncio.run(post())
        name = library.__name__
        calls[f"{name} sync"] = lambda library=library: library.post(url, follow_redirects=True)
        calls[f"{name} async"] = asynchronous

    sdk = {"api_key": "k", "base_url": url + "/v1", "max_retries": 0}
    calls["OpenAI SDK sync"] = lambda: OpenAI(**sdk).chat.completions.create(model="m", messages=MESSAGES)

    def openai_async() -> Any:
        async def create() -> Any:
            return await AsyncOpenAI(**sdk).chat.completions.create(model="m", messages=MESSAGES)
        return asyncio.run(create())
    calls["OpenAI SDK async"] = openai_async

    for model, path in PROVIDERS.items():
        arguments = {"model": model, "messages": MESSAGES, "api_key": "k", "api_base": url + path,
                     "num_retries": 0, "max_retries": 0, "timeout": 10}

        def acompletion(arguments: Dict[str, Any] = arguments) -> Any:
            async def stream() -> None:
                async for _ in await litellm.acompletion(**arguments, stream=True):
                    pass
            return asyncio.run(stream())
        calls[f"litellm {model}"] = lambda arguments=arguments: litellm.completion(**arguments)
        calls[f"litellm {model} async stream"] = acompletion
    calls["litellm embedding"] = lambda: litellm.embedding(
        model="openai/e", input=["x"], api_key="k", api_base=url + "/v1", max_retries=0)
    calls["litellm image"] = lambda: litellm.image_generation(
        model="openai/gpt-image-1", prompt="x", api_key="k", api_base=url + "/v1", max_retries=0)
    return calls


class TestOutboundGuard(unittest.TestCase):

    def _reached(self, server: _Server, url: str, guard: bool) -> Dict[str, bool]:
        reached = {}
        for name, call in _calls(url).items():
            server.hits.clear()
            try:
                with guarded(2 if guard else None):
                    call()
            except Exception:
                pass  # some don't accept the answer; what counts is whether it was asked for
            reached[name] = bool(server.hits)
        return reached

    def _check(self, server: _Server, url: str) -> None:
        unguarded = self._reached(server, url, guard=False)
        self.assertEqual([name for name, hit in unguarded.items() if not hit], [], "never asked the server")
        with self.assertLogs("shared.utils.outbound", "WARNING"):
            reached = self._reached(server, url, guard=True)
        self.assertEqual([name for name, hit in reached.items() if hit], [], "reached an internal address")

    def test_an_internal_address(self) -> None:
        server = _Server()
        self.addCleanup(server.stop)
        self._check(server, server.url)

    def test_a_redirect_to_an_internal_address(self) -> None:
        server = _Server()
        public = _Server(target=server.url)
        self.addCleanup(server.stop)
        self.addCleanup(public.stop)
        real = outbound.internal_address
        # Only the redirect's target counts as internal
        with patch.object(outbound, "internal_address", lambda url: None if url.startswith(public.url) else real(url)):
            unguarded = self._reached(server, public.url, guard=False)
            self.assertEqual([name for name, hit in unguarded.items() if not hit], [], "didn't follow the redirect")
            followed = self._reached(server, public.url, guard=True)
        self.assertEqual([name for name, hit in followed.items() if hit], [], "followed the redirect")

    def test_a_refused_connection_is_an_outage_in_each_library(self) -> None:
        for library in outbound.HTTP_LIBRARIES:
            self.assertTrue(is_provider_outage(library.ConnectError("down")))
            self.assertTrue(is_provider_outage(library.ReadTimeout("slow")))
        self.assertIn(httpx, outbound.HTTP_LIBRARIES)


if __name__ == "__main__":
    unittest.main()
