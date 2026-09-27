#!/usr/bin/env python3
"""
Unit tests for suggesting a fix that changes the memory blocks an agent read.

What matters most: only blocks the agent read, that are not read-only and that the
admin picked, can change; trying a new value never writes it; and applying refuses
to overwrite a block that changed since the suggestion was made, and records each
block change as a block version.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.agent_improver import blocks_read, propose_instruction
from shared.utils.memory_blocks_service import MemoryBlocksService, block_overrides
from shared.utils.models import (AgentConfig, AgentConfigVersion, Base, MemoryBlock, ResponseFeedback,
                                 TestCase)

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _response(name, response, invocation="inv"):
    return {"invocation_id": invocation, "author": "bot",
            "content": {"parts": [{"function_response": {"name": name, "response": response}}]}}


def _reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class TestBlocksRead(unittest.TestCase):

    def test_blocks_whose_values_the_agent_received(self):
        events = [
            _response("get_shared_block", {"status": "success", "label": "hours", "value": "9-5"}),
            _response("list_shared_blocks", {"status": "success", "blocks": [{"label": "faq"}, {"label": "hours"}]}),
            {"invocationId": "inv", "content": {"parts": [{"functionResponse": {
                "name": "get_shared_block", "response": {"result": {"status": "success", "label": "policy"}}}}]}},
        ]
        self.assertEqual(blocks_read(events, "inv"), ["hours", "faq", "policy"])

    def test_other_tools_failures_and_other_turns_are_ignored(self):
        events = [
            _response("search_shared_blocks", {"status": "success", "blocks": [{"label": "preview_only"}]}),
            _response("get_shared_block", {"status": "error", "error_message": "Block not found: x"}),
            _response("get_shared_block", {"status": "success", "label": "earlier"}, invocation="other"),
        ]
        self.assertEqual(blocks_read(events, "inv"), [])


class TestProposeWithBlocks(unittest.TestCase):

    def _propose(self, content, blocks):
        with patch.dict(os.environ, {"EVAL_IMPROVE_MODEL": "m"}), \
                patch("litellm.completion", return_value=_reply(content)) as completion:
            result = propose_instruction("Be helpful.", "", "Open Sunday?", "Yes", None, None, blocks=blocks)
        return result, completion.call_args.kwargs["messages"][0]["content"]

    def test_only_given_blocks_that_changed_are_taken(self):
        result, prompt = self._propose(json.dumps({
            "instruction": "Be helpful.",
            "blocks": {"hours": "Closed on Sundays", "faq": "same", "secrets": "leak", "hours2": 5},
            "reason": "r"}), {"hours": "Open every day", "faq": "same"})
        self.assertEqual(result["blocks"], {"hours": "Closed on Sundays"})
        self.assertIn("<label>hours</label>\n<value>\nOpen every day\n</value>", prompt)
        self.assertIn('"blocks"', prompt)

    def test_a_block_only_suggestion_keeps_the_instruction(self):
        result, _ = self._propose(json.dumps({"blocks": {"hours": "Closed on Sundays"}}), {"hours": "Open"})
        self.assertEqual((result["instruction"], result["blocks"]), ("Be helpful.", {"hours": "Closed on Sundays"}))

    def test_without_blocks_the_prompt_asks_only_for_the_instruction(self):
        _, prompt = self._propose(json.dumps({"instruction": "X"}), None)
        self.assertNotIn("memory_block", prompt)
        self.assertNotIn('"blocks"', prompt)


class FakeDbClient:

    def __init__(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    def get_session(self):
        return self._factory()

    def is_connected(self):
        return True


class TestOverrides(unittest.TestCase):

    def setUp(self):
        patcher = patch.object(MemoryBlocksService, "_set_block_embedding", lambda self, row: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = FakeDbClient()
        self.addCleanup(self.db.engine.dispose)
        self.svc = MemoryBlocksService(self.db)
        self.svc.create_block(1, "hours", "Open every day")
        self.svc.create_block(2, "hours", "Other project")

    def test_reads_see_the_override_only_inside_the_context(self):
        with block_overrides(1, {"hours": "Closed on Sundays"}):
            self.assertEqual(self.svc.get_block(1, "hours")["value"], "Closed on Sundays")
            self.assertEqual(self.svc.list_blocks(1)["blocks"][0]["value"], "Closed on Sundays")
            self.assertEqual(self.svc.get_block(2, "hours")["value"], "Other project")
        self.assertEqual(self.svc.get_block(1, "hours")["value"], "Open every day")


class ReadsHoursLlm:
    """Built lazily: calls get_shared_block("hours"), then answers with the value it got."""

    @staticmethod
    def make():
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        class _Llm(BaseLlm):
            model: str = "reads-hours"

            async def generate_content_async(self, llm_request, stream=False):
                for content in reversed(llm_request.contents):
                    for part in content.parts or []:
                        if part.function_response:
                            value = (part.function_response.response or {}).get("value", "none")
                            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))
                            return
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                    function_call=types.FunctionCall(name="get_shared_block", args={"block_id": "hours"}))]))

        return _Llm()


class TestRealAgentRun(unittest.TestCase):
    """The override and the read tracking through an actual in-memory agent run."""

    def setUp(self):
        patch.object(MemoryBlocksService, "_set_block_embedding", lambda self, row: None).start()
        self.addCleanup(patch.stopall)
        self.db = FakeDbClient()
        self.addCleanup(self.db.engine.dispose)
        patch("shared.utils.database_client.get_database_client", return_value=self.db).start()
        patch("shared.utils.utils.create_model_from_agent_config",
              side_effect=lambda config: ReadsHoursLlm.make()).start()
        patch("shared.utils.file_search_service.FileSearchService").start() \
            .return_value.get_stores_for_agent.return_value = []
        MemoryBlocksService(self.db).create_block(1, "hours", "Open every day")

    def _run(self):
        import asyncio
        from shared.utils.eval_agent_runner import SnapshotAgent
        with patch("shared.utils.agent_manager.get_database_client", return_value=MagicMock()):
            from shared.utils.agent_manager import AgentManager
            manager = AgentManager()
        manager.get_subagents = lambda name: []
        snapshot = {"name": "bot", "type": "llm", "model_name": "reads-hours", "instruction": "Answer.",
                    "project_id": 1, "tool_config": '{"memory_blocks": true}'}
        events = []

        async def ask():
            async with SnapshotAgent(snapshot, manager=manager) as agent:
                return await agent.ask("Open Sunday?", events=events)
        return asyncio.run(ask()), events

    def test_the_agent_reads_the_override_and_the_read_is_found(self):
        reply, events = self._run()
        self.assertEqual(reply, "Open every day")
        self.assertEqual(blocks_read(events), ["hours"])
        with block_overrides(1, {"hours": "Closed on Sundays"}):
            reply, _ = self._run()
        self.assertEqual(reply, "Closed on Sundays")
        self.assertEqual(MemoryBlocksService(self.db).get_block(1, "hours")["value"], "Open every day")


class TestImproveBlockEndpoints(unittest.TestCase):

    def setUp(self):
        patch.object(MemoryBlocksService, "_set_block_embedding", lambda self, row: None).start()
        self.addCleanup(patch.stopall)
        self.db = FakeDbClient()
        self.addCleanup(self.db.engine.dispose)
        db = self.db

        from shared.utils.dashboard.dashboard_server import DashboardServer
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=db):
            self.server = DashboardServer(app, project_root=PROJECT_ROOT)
        self.server.db_client = db
        self.server.TestCase, self.server.AgentConfig = TestCase, AgentConfig
        self.server.AgentConfigVersion, self.server.MemoryBlock = AgentConfigVersion, MemoryBlock
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "admin"
        self.client = TestClient(app)
        self.audit = patch("shared.utils.audit_service.log").start()

        session = db.get_session()
        session.add_all([
            AgentConfig(name="bot", type="llm", instruction="Be helpful.", project_id=1,
                        tool_config='{"memory_blocks": true}'),
            AgentConfig(name="sales", type="llm", project_id=1, tool_config='{"memory_blocks": true}'),
            AgentConfig(name="elsewhere", type="llm", project_id=2, tool_config='{"memory_blocks": true}'),
        ])
        tc = TestCase(agent_name="bot", input="Open Sunday?", expected_output="Closed on Sundays",
                      eval_method="exact_match")
        fb = ResponseFeedback(session_id="s1", message_id="inv", agent_name="bot", rating="down",
                              comment="we are closed on Sundays")
        session.add_all([tc, fb])
        session.commit()
        self.tc_id, self.fb_id = tc.id, fb.id
        session.close()

        self.svc = MemoryBlocksService(db)
        self.svc.create_block(1, "system_instruction_bot", "Open every day")
        self.svc.create_block(1, "notes", "misc")
        self.svc.create_block(1, "locked", "fixed", metadata={"read_only": True})

        self.read_events = [
            {"invocation_id": "inv", "author": "user", "content": {"parts": [{"text": "Open Sunday?"}]}},
            _response("get_shared_block", {"status": "success", "label": "system_instruction_bot"}),
            _response("list_shared_blocks", {"status": "success",
                                             "blocks": [{"label": "notes"}, {"label": "locked"}]}),
            {"invocation_id": "inv", "author": "bot", "content": {"parts": [{"text": "Yes"}]}},
        ]
        self.server._get_session_events = lambda sid: self.read_events

    def _value(self, label):
        return self.svc.get_block(1, label)["value"]

    def _propose(self, body, returned=None):
        returned = returned or {"instruction": "Be helpful.", "blocks": {}, "reason": ""}
        with patch("shared.utils.agent_improver.propose_instruction", return_value=returned) as propose:
            resp = self.client.post("/dashboard/api/evals/improve/propose", json=body)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json(), propose.call_args.args

    def test_candidates_are_the_editable_blocks_the_agent_read(self):
        data, args = self._propose({"feedback_id": self.fb_id})
        self.assertEqual([(b["label"], b["selected"]) for b in data["candidate_blocks"]],
                         [("system_instruction_bot", True), ("notes", False)])
        self.assertEqual(args[7], {"system_instruction_bot": "Open every day"})
        self.assertEqual(data["shared_with"], ["sales"])

    def test_the_admin_picks_the_blocks(self):
        _, args = self._propose({"feedback_id": self.fb_id, "block_labels": ["notes", "locked", "absent"]})
        self.assertEqual(args[7], {"notes": "misc"})

    def test_a_test_case_is_rerun_to_find_the_blocks(self):
        read = self.read_events

        class Rerun:
            def __init__(self, snapshot, manager=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def ask(self, input_text, timeout=120.0, events=None):
                events.extend(read)
                return "Yes"

        with patch("shared.utils.eval_agent_runner.SnapshotAgent", Rerun):
            data, args = self._propose({"test_case_id": self.tc_id})
        self.assertEqual(args[7], {"system_instruction_bot": "Open every day"})

    def test_check_runs_with_the_new_block_value_without_writing_it(self):
        svc = self.svc

        class ReadsBlock:
            def __init__(self, snapshot, manager=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def ask(self, input_text, timeout=120.0, events=None):
                return svc.get_block(1, "system_instruction_bot")["value"]

        with patch("shared.utils.eval_agent_runner.SnapshotAgent", ReadsBlock):
            data = self.client.post("/dashboard/api/evals/improve/check", json={
                "agent_name": "bot", "instruction": "Be helpful.",
                "blocks": {"system_instruction_bot": "Closed on Sundays"}}).json()
        self.assertEqual((data["before"]["passed"], data["after"]["passed"]), (0, 1))
        self.assertEqual(self._value("system_instruction_bot"), "Open every day")

    def test_a_read_only_or_foreign_block_is_refused(self):
        for label in ("locked", "absent"):
            with self.subTest(label=label):
                resp = self.client.post("/dashboard/api/evals/improve/check", json={
                    "agent_name": "bot", "instruction": "x", "blocks": {label: "y"}})
                self.assertEqual(resp.status_code, 400)

    def test_apply_writes_block_versions_and_leaves_an_unchanged_instruction(self):
        resp = self.client.post("/dashboard/api/evals/improve/apply", json={
            "agent_name": "bot", "instruction": "Be helpful.", "base_instruction": "Be helpful.",
            "blocks": {"system_instruction_bot": "Closed on Sundays"},
            "base_blocks": {"system_instruction_bot": "Open every day"}, "feedback_id": self.fb_id})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._value("system_instruction_bot"), "Closed on Sundays")
        latest = self.svc.list_versions(1, "system_instruction_bot")["versions"][0]
        self.assertEqual((latest["version_number"], latest["changed_by"]), (2, "admin"))

        session = self.db.get_session()
        self.assertEqual(session.query(AgentConfigVersion).count(), 0)
        session.close()
        details = self.audit.call_args.kwargs["details"]
        self.assertEqual((details["fields"], details["memory_blocks"], details["feedback_id"]),
                         ([], {"system_instruction_bot": 2}, self.fb_id))

    def test_apply_refuses_when_a_block_changed_meanwhile(self):
        self.svc.modify_block(1, "system_instruction_bot", value="Edited by someone else")
        resp = self.client.post("/dashboard/api/evals/improve/apply", json={
            "agent_name": "bot", "instruction": "New instruction", "base_instruction": "Be helpful.",
            "blocks": {"system_instruction_bot": "Closed on Sundays"},
            "base_blocks": {"system_instruction_bot": "Open every day"}})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self._value("system_instruction_bot"), "Edited by someone else")
        session = self.db.get_session()
        self.assertEqual(session.query(AgentConfig).filter_by(name="bot").one().instruction, "Be helpful.")
        session.close()


if __name__ == "__main__":
    unittest.main()
