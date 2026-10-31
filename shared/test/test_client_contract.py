#!/usr/bin/env python3
"""
The calls MATE's live clients make, against the real app (#277).

Clients reach a MATE installation through three entry points:
- the OpenAI API with a personal access token;
- the widget, with its public key;
- /run_sse through the proxy, with the admin's credentials (Basic auth, or the
  bearer token /auth/token gives for them).

2.0 promises that an installation with one workspace answers these as 1.x does
(documents/design/2.0-plan.md, "1.x clients"). Each test pins what a client depends
on: the status, the shape of the answer, and what reaches the agent server. A test
here that has to change is a breaking change for those clients and goes in the
release notes.
"""

import base64
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# auth_server sets OTEL_SDK_DISABLED on import; keep that out of other tests.
with patch.dict(os.environ):
    import auth_server
from server import auth
from server.pat_auth import verify_pat_hash
from shared.utils import database_client, rate_limit_service, token_usage_service
from shared.utils.auth_utils import revoke_token
from shared.utils.models import (
    AgentConfig, Base, PersonalAccessToken, Project, User, WidgetApiKey,
)

PAT = "mate_pat_contract_test_token"
WIDGET_KEY = "wk_contract_test"
AGENT = "bot"
DEVELOPER = "dev@corp.com"

# One final event, as ADK sends it on /run_sse
REPLY = {"invocationId": "inv-1", "author": AGENT, "partial": False,
         "content": {"role": "model", "parts": [{"text": "Hello from the agent"}]}}
REPLY_SSE = f"data: {json.dumps(REPLY)}\n\n".encode()


class _ClientContract(unittest.TestCase):
    """The real auth server on an in-memory database, with a fake agent server."""

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        Session = sessionmaker(bind=engine)
        db = MagicMock()
        db.is_connected.return_value = True
        db.get_session.side_effect = lambda: Session()

        session = Session()
        session.add(Project(id=1, name="Default"))
        session.add(AgentConfig(name=AGENT, type="llm", model_name="gemini/gemini-2.5-flash",
                                instruction="Help.", project_id=1, expose_as_model=True,
                                disabled=False))
        session.add(User(user_id=DEVELOPER, roles='["developer"]'))
        session.add(PersonalAccessToken(token_hash=verify_pat_hash(PAT), token_prefix=PAT[:16],
                                        name="client", user_id=DEVELOPER))
        session.add(WidgetApiKey(api_key=WIDGET_KEY, project_id=1, agent_name=AGENT,
                                 allowed_origins=json.dumps(["https://client.example"])))
        session.commit()
        session.close()

        # What reached the agent server, as (method, path, json body or None)
        self.agent_server_saw = []
        transport = httpx.MockTransport(self._agent_server)
        real_client = httpx.AsyncClient
        for patcher in (
            patch.object(database_client, "_database_client", db),
            # Services that keep the database client they were built with
            patch.object(rate_limit_service, "_rate_limit_service", None),
            patch.object(token_usage_service, "_token_usage_service", None),
            patch.object(httpx, "AsyncClient",
                         lambda *a, **kw: real_client(transport=transport, timeout=kw.get("timeout"))),
            patch("shared.utils.audit_service.log"),
            patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(auth_server.app)

    def _agent_server(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.agent_server_saw.append((request.method, request.url.path, body))
        parts = request.url.path.strip("/").split("/")
        if request.url.path == "/run_sse":
            return httpx.Response(200, content=REPLY_SSE,
                                  headers={"content-type": "text/event-stream"})
        if request.url.path == "/list-apps":
            return httpx.Response(200, json=[AGENT])
        if parts[:1] == ["apps"] and parts[2:3] == ["users"] and parts[4:5] == ["sessions"]:
            session_id = parts[5] if len(parts) > 5 else "s-new"
            if request.method == "GET" and len(parts) > 5:
                return httpx.Response(404, json={"detail": "Session not found"})
            return httpx.Response(200, json={"id": session_id, "appName": parts[1],
                                             "userId": parts[3], "state": {}, "events": []})
        return httpx.Response(404)

    def _runs(self):
        return [body for method, path, body in self.agent_server_saw if path == "/run_sse"]


def _basic(username: str, password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


class TestOpenAIApiWithToken(_ClientContract):

    def _headers(self):
        return {"Authorization": f"Bearer {PAT}"}

    def test_models_lists_the_exposed_agent(self):
        resp = self.client.get("/v1/models", headers=self._headers())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["object"], "list")
        self.assertEqual([m["id"] for m in body["data"]], [AGENT])
        self.assertEqual(body["data"][0]["object"], "model")

    def test_completion(self):
        resp = self.client.post("/v1/chat/completions", headers=self._headers(), json={
            "model": AGENT, "messages": [{"role": "user", "content": "Hi"}]})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["model"], AGENT)
        choice = body["choices"][0]
        self.assertEqual(choice["message"], {"role": "assistant", "content": "Hello from the agent"})
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(set(body["usage"]), {"prompt_tokens", "completion_tokens", "total_tokens"})
        # The agent runs as the token's owner
        run = self._runs()[0]
        self.assertEqual((run["app_name"], run["user_id"]), (AGENT, DEVELOPER))

    def test_streamed_completion(self):
        with self.client.stream("POST", "/v1/chat/completions", headers=self._headers(), json={
                "model": AGENT, "stream": True,
                "messages": [{"role": "user", "content": "Hi"}]}) as resp:
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
            frames = [line[len("data: "):] for line in resp.iter_lines() if line.startswith("data: ")]
        self.assertEqual(frames[-1], "[DONE]")
        chunks = [json.loads(frame) for frame in frames[:-1]]
        self.assertTrue(all(c["object"] == "chat.completion.chunk" for c in chunks))
        text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
        self.assertEqual(text, "Hello from the agent")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_a_conversation_id_keeps_the_session(self):
        # A client that tracks its own conversations sends the same header each turn
        from server.openai_translate import CONVERSATION_ID_HEADER
        for content in ("Hi", "And again"):
            self.client.post("/v1/chat/completions",
                             headers={**self._headers(), CONVERSATION_ID_HEADER: "conv-42"},
                             json={"model": AGENT, "messages": [{"role": "user", "content": content}]})
        first, second = self._runs()
        self.assertEqual(first["session_id"], second["session_id"])

    def test_an_unknown_token_is_refused(self):
        resp = self.client.get("/v1/models", headers={"Authorization": "Bearer mate_pat_nope"})
        self.assertEqual(resp.status_code, 401)


class TestWidget(_ClientContract):

    def _chat(self, **body):
        return self.client.post("/widget/api/chat", json={"message": "Hi", **body},
                                headers={"X-Widget-Key": WIDGET_KEY,
                                         "Origin": "https://client.example"})

    def test_chat_streams_the_session_then_the_reply(self):
        resp = self._chat(user_id="visitor-7")
        self.assertEqual(resp.status_code, 200, resp.text)
        first, rest = resp.content.split(b"\n\n", 1)
        self.assertEqual(json.loads(first[len(b"data: "):]), {"session_id": "s-new", "debug_mode": False})
        self.assertEqual(rest, REPLY_SSE)
        # The agent runs as the widget visitor, scoped to the key
        run = self._runs()[0]
        self.assertEqual(run["app_name"], AGENT)
        self.assertRegex(run["user_id"], r"^widget_\d+_visitor-7$")

    def test_chat_continues_a_session(self):
        self._chat(session_id="s-existing")
        self.assertEqual(self._runs()[0]["session_id"], "s-existing")

    def test_an_unknown_key_is_refused(self):
        resp = self.client.post("/widget/api/chat", json={"message": "Hi"},
                                headers={"X-Widget-Key": "wk_nope"})
        self.assertEqual(resp.status_code, 401)


class TestRunSseThroughTheProxy(_ClientContract):

    def _run(self, headers):
        resp = self.client.post("/run_sse", headers=headers, json={
            "app_name": AGENT, "user_id": "end-user-1", "session_id": "s1",
            "new_message": {"role": "user", "parts": [{"text": "Hi"}]}, "streaming": True})
        return resp

    def _check_run(self, headers):
        create = self.client.post(f"/apps/{AGENT}/users/end-user-1/sessions", headers=headers, json={})
        self.assertEqual(create.status_code, 200, create.text)
        self.assertEqual(create.json()["id"], "s-new")

        resp = self._run(headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.content, REPLY_SSE)
        # The admin may run as any user id, the client's own end user here
        run = self._runs()[0]
        self.assertEqual((run["app_name"], run["user_id"]), (AGENT, "end-user-1"))

    def test_with_basic_auth(self):
        self._check_run(_basic(auth.AUTH_USERNAME, auth.AUTH_PASSWORD))

    def test_with_the_bearer_token_from_auth_token(self):
        token_resp = self.client.post("/auth/token", headers=_basic(auth.AUTH_USERNAME, auth.AUTH_PASSWORD))
        self.assertEqual(token_resp.status_code, 200, token_resp.text)
        token = token_resp.json()["access_token"]
        self.addCleanup(revoke_token, token)
        self.client.cookies.clear()  # only the header, as a server-side client sends it
        self._check_run({"Authorization": f"Bearer {token}"})

    def test_list_apps(self):
        resp = self.client.get("/list-apps", headers=_basic(auth.AUTH_USERNAME, auth.AUTH_PASSWORD))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [AGENT])

    def test_wrong_credentials_are_refused(self):
        resp = self._run(_basic(auth.AUTH_USERNAME, "wrong"))
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._runs(), [])


if __name__ == "__main__":
    unittest.main()
