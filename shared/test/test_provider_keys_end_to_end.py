#!/usr/bin/env python3
"""
Phase 4 of the workspaces (#277), end to end: a workspace with its own OpenRouter key
and no use of the platform's runs a conversation, an image tool call and an eval on
that key alone (documents/design/2.0-plan.md, 4f).

The workspace is set up over HTTP, as a platform administrator and its owner would:
Platform → Workspaces, Models and keys, Projects, Agents, Evals. Running a test case
builds the agent from its stored config and runs it in the process, as the agent
server does: its model, its image tool and the eval's judge each call OpenRouter.

Every outgoing request is caught at httpx and answered as OpenRouter would. The
platform's own keys are in the environment, so a request that carried one would
show; none may. A workspace without a key gets "This workspace has no key", and a
key never shows in a response, an audit entry or a log line, even when the provider
echoes it in an error.
"""

import base64
import json
import logging
import os
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from shared.test.test_cross_workspace import ALICE, A, _TwoWorkspaces, _platform_admin, _signed_in, auth_server
from server.request_context import WORKSPACE_HEADER

CLIENT_KEY = "sk-or-v1-client0000000000000000000000000000000000000000001234"
PLATFORM_KEYS = {
    "OPENROUTER_API_KEY": "sk-or-platform-must-not-be-sent",
    "OPENAI_API_KEY": "sk-openai-platform-must-not-be-sent",
    "GOOGLE_API_KEY": "google-platform-must-not-be-sent",
    "GEMINI_API_KEY": "gemini-platform-must-not-be-sent",
    "ANTHROPIC_API_KEY": "sk-ant-platform-must-not-be-sent",
}
AGENT = "client_assistant"
MODEL = "openrouter/google/gemini-2.5-flash"
IMAGE_MODEL = "openrouter/google/gemini-2.5-flash-image"
JUDGE = "openrouter/openai/gpt-4o-mini"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _choice(message: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": "gen-1", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


class _OpenRouter:
    """OpenRouter, as httpx sees it. Records every request that leaves the server."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        self.echo_key = False

    def answer(self, request: httpx.Request) -> httpx.Response:
        response = self._answer(request)
        response.request = request
        return response

    def _answer(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append({"host": request.url.host, "path": request.url.path,
                           "authorization": request.headers.get("authorization"),
                           "model": body.get("model"), "kind": None})
        call = self.calls[-1]
        if request.url.host != "openrouter.ai":
            return httpx.Response(599, json={"error": "not a provider this test expects"})
        if request.method == "GET" and request.url.path == "/api/v1/models":
            # MATE's model prices (shared/utils/model_pricing.py): public, no key
            call["kind"] = "prices"
            return httpx.Response(200, json={"data": []})
        key = (request.headers.get("authorization") or "").removeprefix("Bearer ")
        if self.echo_key:
            # Some providers repeat the key they were sent in their error
            call["kind"] = "refused"
            return httpx.Response(401, json={"error": {"message": f"Invalid API key: {key}", "code": 401}})
        messages = body.get("messages") or []
        if body.get("modalities"):
            call["kind"] = "image"
            image = "data:image/png;base64," + base64.b64encode(PNG).decode()
            return httpx.Response(200, json=_choice({"content": "", "images": [
                {"type": "image_url", "image_url": {"url": image}}]}))
        if body.get("tools") and not any(m.get("role") == "tool" for m in messages):
            call["kind"] = "agent asks for an image"
            return httpx.Response(200, json=_choice({"content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "generate_image", "arguments": json.dumps({"prompt": "a red cat"})}}]}))
        if body.get("tools"):
            call["kind"] = "agent answers"
            return httpx.Response(200, json=_choice({"content": "Here is your red cat."}))
        call["kind"] = "judge"
        return httpx.Response(200, json=_choice({"content": json.dumps({"score": 1.0, "reasoning": "matches"})}))


class TestPhase4EndToEnd(_TwoWorkspaces):

    def setUp(self):
        super().setUp()
        self.provider = _OpenRouter()
        env = {k: v for k, v in os.environ.items() if not k.startswith(
            ("OPENROUTER", "OPENAI", "GOOGLE", "GEMINI", "ANTHROPIC", "OUTBOUND_ALLOW", "EVAL_"))}
        env.update(PLATFORM_KEYS)
        env["MATE_SECRET_KEY"] = Fernet.generate_key().decode()
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Requests to the app go to the app; every other one is the provider's
        real_send, real_asend = httpx.Client.send, httpx.AsyncClient.send
        provider = self.provider

        def send(client, request, **kwargs):
            if request.url.host == "testserver":
                return real_send(client, request, **kwargs)
            return provider.answer(request)

        async def asend(client, request, **kwargs):
            return provider.answer(request)

        for patcher in (patch.object(httpx.Client, "send", send),
                        patch.object(httpx.AsyncClient, "send", asend)):
            patcher.start()
            self.addCleanup(patcher.stop)

        # Every log line of the run, to look for the key in
        self.log_lines: List[str] = []
        lines = self.log_lines

        class Capture(logging.Handler):
            def emit(self, record):
                try:
                    lines.append(self.format(record))
                except Exception:
                    lines.append(str(record.msg))

        handler = Capture(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, level)
        self.addCleanup(root.removeHandler, handler)
        self.responses: List[str] = []

    def _ok(self, response: httpx.Response, status: int = 200) -> dict:
        self.responses.append(response.text)
        self.assertEqual(response.status_code, status, response.text[:400])
        return response.json()

    def test_a_workspace_runs_on_its_own_openrouter_key_alone(self):
        admin = TestClient(auth_server.app)
        # 1. The platform administrator creates a workspace that may not use the platform's keys
        created = self._ok(admin.post("/dashboard/api/platform/workspaces", headers=_platform_admin(A), json={
            "name": "Client", "slug": "client", "owner": ALICE, "allow_platform_keys": False}))
        workspace_id = created["workspace"]["id"]
        owner = TestClient(auth_server.app, cookies=_signed_in(ALICE),
                           headers={WORKSPACE_HEADER: str(workspace_id)})

        # 2. Its owner adds the OpenRouter key and picks an OpenRouter image model
        saved = self._ok(owner.put("/dashboard/api/workspace-models/keys",
                                   json={"provider": "openrouter", "api_key": CLIENT_KEY}))
        self.assertEqual(saved["key"], "••••1234")
        self._ok(owner.put("/dashboard/api/workspace-models/image-model", json={"model": IMAGE_MODEL}))
        listed = self._ok(owner.get("/dashboard/api/workspace-models"))
        self.assertEqual(([k["provider"] for k in listed["keys"]], listed["may_use_platform_keys"]),
                         (["openrouter"], False))

        # 3. A project, an agent with the image tool, and a test case judged by a model
        project_id = self._ok(owner.post("/dashboard/api/projects", data={"name": "Client project"}))["project"]["id"]
        self._ok(owner.post("/dashboard/api/agents", data={
            "name": AGENT, "type": "llm", "project_id": str(project_id), "model_name": MODEL,
            "description": "Draws for the client", "instruction": "Draw what you are asked for.",
            "tool_config": json.dumps({"image_tools": True}), "allowed_for_roles": json.dumps(["user"])}))
        config_id = next(a["id"] for a in self._ok(owner.get("/dashboard/api/agents"))["configs"]
                         if a["name"] == AGENT)
        version_id = self._ok(owner.get(f"/dashboard/api/agents/{config_id}/versions"))["versions"][0]["id"]
        case = self._ok(owner.post("/dashboard/api/evals", json={
            "agent_name": AGENT, "input": "Draw a red cat.", "expected_output": "A red cat.",
            "eval_method": "llm_judge", "judge_model": JUDGE, "threshold": 0.5}))["test_case"]

        # 4. Running it: the conversation, the image tool call, the judge
        result = self._ok(owner.post(f"/dashboard/api/evals/{case['id']}/run",
                                     json={"version_id": version_id}))["result"]
        self.assertTrue(result["passed"], result)
        self.assertIn("red cat", result["actual_output"])
        calls = [c for c in self.provider.calls if c["kind"] != "prices"]
        self.assertEqual([c["kind"] for c in calls],
                         ["agent asks for an image", "image", "agent answers", "judge"])
        self.assertEqual({(c["host"], c["authorization"]) for c in calls},
                         {("openrouter.ai", f"Bearer {CLIENT_KEY}")})
        self.assertFalse(any(c["authorization"] for c in self.provider.calls if c["kind"] == "prices"))
        self.assertEqual([c["model"] for c in calls],
                         ["google/gemini-2.5-flash", "google/gemini-2.5-flash-image",
                          "google/gemini-2.5-flash", "openai/gpt-4o-mini"])

        # 5. A provider that echoes the key in its error: the key shows nowhere
        self.provider.echo_key = True
        refused = owner.post(f"/dashboard/api/evals/{case['id']}/run", json={"version_id": version_id})
        self.responses.append(refused.text)
        self.provider.echo_key = False
        self.assertEqual(refused.status_code, 500)
        # The provider's error is logged and answered with the key masked
        self.assertIn("Invalid API key: ••••1234", "\n".join(self.log_lines))

        # 6. Without the key the workspace makes no call: the platform's isn't used
        self._ok(owner.delete("/dashboard/api/workspace-models/keys/openrouter"))
        before = len([c for c in self.provider.calls if c["kind"] != "prices"])
        failed = owner.post(f"/dashboard/api/evals/{case['id']}/run", json={"version_id": version_id})
        self.responses.append(failed.text)
        self.assertIn("This workspace has no key for openrouter", failed.text)
        self.assertEqual(len([c for c in self.provider.calls if c["kind"] != "prices"]), before)

        # 7. Nothing carried a platform key, and the workspace's key shows nowhere
        audit = self._ok(admin.get("/dashboard/api/audit-logs", headers=_platform_admin(A),
                                   params={"all_workspaces": "true", "limit": 500}))
        everything = "\n".join(self.responses + self.log_lines + [json.dumps(audit)]
                               + [json.dumps(c) for c in self.provider.calls if c["kind"] != "refused"])
        for platform_key in PLATFORM_KEYS.values():
            self.assertNotIn(platform_key, everything)
        self.assertNotIn(CLIENT_KEY, "\n".join(self.responses + self.log_lines + [json.dumps(audit)]))
        self.assertIn("provider_key.set", json.dumps(audit))


if __name__ == "__main__":
    unittest.main()
