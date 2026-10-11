#!/usr/bin/env python3
"""
Tests for the A2A routes (shared/utils/a2a_routes.py) on the pinned a2a-sdk.

adk_main.py logs and carries on when the A2A setup fails, so a dependency bump
that breaks it would only print a line at startup. These tests fail instead:
the routes must import, mount, serve each agent's card, and answer JSON-RPC in
both the 1.x method names and the 0.3 ones the MCP bridge sends.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.utils.a2a_routes import card_data_for, default_card_data, register_a2a_routes


class EchoExecutor:
    """Answers every message with its own text, as an A2A agent executor does."""

    def __init__(self, name):
        self.name = name

    async def execute(self, context, event_queue):
        from a2a.server.tasks import TaskUpdater
        from google.adk.a2a import _compat

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await _compat.enqueue_submitted_signal(event_queue, context=context)
        text = context.get_user_input()
        await updater.add_artifact([_compat.make_text_part(f"{self.name}: {text}")])
        await updater.complete()

    async def cancel(self, context, event_queue):
        return None


class TestA2ARoutes(unittest.TestCase):

    def setUp(self):
        self.app = FastAPI()
        self.agents_dir = Path(tempfile.mkdtemp())
        (self.agents_dir / "custom").mkdir()
        (self.agents_dir / "custom" / "agent.json").write_text(
            json.dumps({"description": "A custom card", "version": "2.1.0"}), encoding="utf-8")
        self.mounted = register_a2a_routes(self.app, ["bot", "custom"], EchoExecutor,
                                           agents_dir=self.agents_dir)
        self.client = TestClient(self.app)

    def _rpc(self, agent, method, params):
        return self.client.post(f"/a2a/{agent}", json={"jsonrpc": "2.0", "id": "1", "method": method,
                                                      "params": params})

    def test_every_agent_gets_an_endpoint_and_a_card(self):
        self.assertEqual(self.mounted, 2)
        paths = {getattr(r, "path", None) for r in self.app.routes}
        for agent in ("bot", "custom"):
            self.assertIn(f"/a2a/{agent}", paths)
            self.assertIn(f"/a2a/{agent}/.well-known/agent-card.json", paths)

    def test_the_card_names_the_agents_own_endpoint(self):
        card = self.client.get("/a2a/bot/.well-known/agent-card.json")
        self.assertEqual(card.status_code, 200, card.text)
        text = json.dumps(card.json())
        self.assertIn("bot", card.json()["name"])
        self.assertIn("/a2a/bot", text, "a client that reads the card must find the agent's endpoint")

    def test_an_agent_json_is_merged_over_the_default_card(self):
        card = self.client.get("/a2a/custom/.well-known/agent-card.json").json()
        self.assertEqual((card["description"], card["version"]), ("A custom card", "2.1.0"))
        self.assertEqual(card["name"], "custom", "fields agent.json leaves out keep their defaults")

    def test_message_send_the_0_3_method_name_still_works(self):
        # The MCP bridge (agent_mcp_protocol_handler.py) and existing callers send this
        r = self._rpc("bot", "message/send", {"message": {
            "role": "user", "messageId": "m1", "kind": "message", "parts": [{"kind": "text", "text": "hello"}]}})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertNotIn("error", body, body)
        self.assertIn("bot: hello", json.dumps(body))

    def test_send_message_the_1_x_method_name_works(self):
        from a2a.utils.constants import VERSION_HEADER
        r = self.client.post("/a2a/custom", headers={VERSION_HEADER: "1.0"}, json={
            "jsonrpc": "2.0", "id": "1", "method": "SendMessage",
            "params": {"message": {"role": "ROLE_USER", "messageId": "m2", "parts": [{"text": "hi"}]}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("error", r.json(), r.json())
        self.assertIn("custom: hi", json.dumps(r.json()))

    def test_an_agent_that_fails_to_set_up_does_not_stop_the_others(self):
        def factory(name):
            if name == "broken":
                raise RuntimeError("no runner")
            return EchoExecutor(name)
        app = FastAPI()
        self.assertEqual(register_a2a_routes(app, ["broken", "ok"], factory), 1)
        self.assertIn("/a2a/ok", {getattr(r, "path", None) for r in app.routes})


class TestCardData(unittest.TestCase):

    def test_the_default_card_points_at_the_agents_endpoint(self):
        data = default_card_data("bot", base_url="https://mate.example.com/")
        self.assertEqual(data["url"], "https://mate.example.com/a2a/bot")

    def test_an_unreadable_agent_json_keeps_the_default(self):
        agents_dir = Path(tempfile.mkdtemp())
        (agents_dir / "bot").mkdir()
        (agents_dir / "bot" / "agent.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(card_data_for("bot", agents_dir)["description"], "A2A-enabled bot agent")


class TestMcpBridgeReadsTheReply(unittest.TestCase):
    """The MCP bridge (agent_mcp_protocol_handler) turns an A2A answer into MCP text."""

    def test_a_tasks_artifacts_are_the_reply(self):
        from shared.utils.tools.agent_mcp_protocol_handler import a2a_result_text
        task = {"kind": "task", "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": "Zdravo"}, {"kind": "data", "data": {}}]},
                              {"parts": [{"kind": "text", "text": "svete"}]}],
                "history": [{"role": "user", "parts": [{"kind": "text", "text": "the question"}]}]}
        self.assertEqual(a2a_result_text(task), "Zdravo\nsvete")

    def test_a_failed_tasks_status_message_is_the_reply(self):
        from shared.utils.tools.agent_mcp_protocol_handler import a2a_result_text
        task = {"kind": "task", "status": {"state": "failed",
                                           "message": {"parts": [{"kind": "text", "text": "Model error"}]}}}
        self.assertEqual(a2a_result_text(task), "Model error")

    def test_a_message_result_is_read_too(self):
        from shared.utils.tools.agent_mcp_protocol_handler import a2a_result_text
        self.assertEqual(a2a_result_text({"kind": "message", "parts": [{"kind": "text", "text": "hi"}]}), "hi")
        self.assertEqual(a2a_result_text({"message": {"parts": [{"kind": "text", "text": "hey"}]}}), "hey")
        self.assertIsNone(a2a_result_text({"kind": "task", "artifacts": []}))


if __name__ == "__main__":
    unittest.main()
