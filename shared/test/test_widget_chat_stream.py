#!/usr/bin/env python3
"""
Tests for how /widget/api/chat passes the agent server's /run_sse reply on.

The route puts its own session event in front of the stream, so headers that
describe the agent server's body (Content-Length above all) must not be copied:
an error reply with a Content-Length used to be cut off at that length.
"""

import gzip
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import widget_routes as wr


class _Key:
    id = 1
    agent_name = "test_agent"

    def get_allowed_origins(self):
        return None

    def get_widget_config(self):
        return {}


def _client(upstream: httpx.Response) -> TestClient:
    """A widget router whose agent server answers every request with upstream."""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: upstream)
    db = MagicMock()
    db.get_session.return_value = None
    for patcher in (
        patch.object(wr, "_lookup_widget_key", return_value=_Key()),
        patch.object(wr, "_create_adk_session", AsyncMock(return_value="s1")),
        patch.object(wr.httpx, "AsyncClient", lambda **kw: real_client(transport=transport)),
        patch("shared.utils.database_client.get_database_client", return_value=db),
    ):
        patcher.start()
    app = FastAPI()
    app.include_router(wr.router)
    return TestClient(app)


class TestWidgetChatStream(unittest.TestCase):

    def tearDown(self):
        patch.stopall()

    def _post(self, client):
        return client.post("/widget/api/chat", headers={"X-Widget-Key": "wk"},
                           json={"message": "hi", "session_id": "s1"})

    def test_stream_gets_session_event_and_whole_body(self):
        body = b'data: {"partial": false, "content": {"parts": [{"text": "hello"}]}}\n\n'
        client = _client(httpx.Response(200, content=body,
                                        headers={"content-type": "text/event-stream"}))
        r = self._post(client)
        self.assertEqual(r.status_code, 200)
        first, rest = r.content.split(b"\n\n", 1)
        self.assertEqual(json.loads(first[len(b"data: "):]), {"session_id": "s1", "debug_mode": False})
        self.assertEqual(rest, body)

    def test_error_reply_passed_through_whole(self):
        detail = {"detail": "x" * 200}
        client = _client(httpx.Response(500, json=detail))
        r = self._post(client)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json(), detail)

    def test_upstream_encoding_headers_not_copied(self):
        # httpx decodes the body, so a copied Content-Encoding would describe bytes that are gone
        body = b'data: {"partial": false}\n\n'
        client = _client(httpx.Response(200, content=gzip.compress(body), headers={
            "content-type": "text/event-stream", "content-encoding": "gzip"}))
        r = self._post(client)
        self.assertNotIn("content-encoding", r.headers)
        self.assertTrue(r.content.endswith(body))


if __name__ == "__main__":
    unittest.main()
