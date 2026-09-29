#!/usr/bin/env python3
"""
Unit tests for running evals against a stored agent version.

Evals used to call the deployed agent by name and file the result under whichever
version was selected, so scoring version 1 while version 2 was live scored
version 2. The agent is now built from the version's snapshot and run in memory.
The proof that matters: two versions with different instructions give different
answers, each the one its own instruction produces.
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.eval_agent_runner import (AdkSnapshotAgent, ReplyCollector, SnapshotAgent,
                                            config_from_snapshot, is_eval_run)
from shared.utils.langgraph.snapshot_agent import LangGraphSnapshotAgent
from shared.utils.models import (AgentConfig, AgentConfigVersion, Base, EvalResult,
                                 TestCase)

PROJECT_ROOT = Path(__file__).parent.parent.parent


class EchoInstructionLlm(BaseLlm):
    """Replies with the first line of its system instruction, so a reply names its config."""

    model: str = "echo"

    async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        instruction = str(llm_request.config.system_instruction or "")
        yield LlmResponse(content=types.Content(
            role="model", parts=[types.Part(text=instruction.strip().splitlines()[0])]))


class EchoInstructionChatModel(BaseChatModel):
    """LangChain twin of EchoInstructionLlm, for the LangGraph runtime."""

    @property
    def _llm_type(self) -> str:
        return "echo"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        text = system.strip().splitlines()[0]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def _snapshot(instruction, name="support_bot", **extra):
    return {"name": name, "type": "llm", "model_name": "echo", "instruction": instruction,
            "description": "answers questions", "project_id": 1, **extra}


class TestReplyCollector(unittest.TestCase):

    def _collect(self, events):
        c = ReplyCollector()
        for e in events:
            c.feed(e)
        return c.text

    def test_last_authors_text_after_tool_calls(self):
        self.assertEqual(self._collect([
            {"author": "root", "content": {"parts": [{"text": "Let me check."}]}},
            {"author": "root", "content": {"parts": [{"function_call": {"name": "search"}}]}},
            {"author": "root", "content": {"parts": [{"function_response": {"name": "search"}}]}},
            {"author": "root", "content": {"parts": [{"text": "We open at 9."}]}},
        ]), "We open at 9.")

    def test_camel_case_wire_keys_count_as_tool_events(self):
        self.assertEqual(self._collect([
            {"author": "root", "content": {"parts": [{"text": "Checking"}]}},
            {"author": "root", "content": {"parts": [{"functionCall": {"name": "x"}}]}},
        ]), "")

    def test_author_change_and_routing(self):
        self.assertEqual(self._collect([
            {"author": "root", "content": {"parts": [{"text": "Passing you on."}]}},
            {"author": "root", "actions": {"transfer_to_agent": "sub"}},
            {"author": "sub", "content": {"parts": [{"text": "Sub here."}]}},
        ]), "Sub here.")

    def test_partial_then_complete_is_not_doubled(self):
        self.assertEqual(self._collect([
            {"author": "root", "partial": True, "content": {"parts": [{"text": "Hello"}]}},
            {"author": "root", "content": {"parts": [{"text": "Hello there"}]}},
        ]), "Hello there")


class TestConfigFromSnapshot(unittest.TestCase):

    def test_unknown_keys_are_dropped(self):
        config = config_from_snapshot(_snapshot("x", something_new=True))
        self.assertEqual(config.instruction, "x")
        self.assertFalse(hasattr(config, "something_new"))


def _manager(subagents=None):
    with patch("shared.utils.agent_manager.get_database_client", return_value=MagicMock()):
        from shared.utils.agent_manager import AgentManager
        manager = AgentManager()
    subagents = subagents or {}
    manager.get_subagents = lambda name: subagents.get(name, [])
    return manager


class TestSnapshotAgent(unittest.TestCase):

    def setUp(self):
        patches = [
            patch("shared.utils.utils.create_model_from_agent_config",
                  side_effect=lambda config: EchoInstructionLlm()),
            patch("shared.utils.file_search_service.FileSearchService"),
        ]
        for p in patches:
            mocked = p.start()
            self.addCleanup(p.stop)
        mocked.return_value.get_stores_for_agent.return_value = []

    async def _ask(self, snapshot, manager=None):
        async with SnapshotAgent(snapshot, manager=manager or _manager()) as agent:
            return await agent.ask("What are your opening hours?")

    def test_each_version_answers_with_its_own_config(self):
        v1 = asyncio.run(self._ask(_snapshot("VERSION ONE instruction")))
        v2 = asyncio.run(self._ask(_snapshot("VERSION TWO instruction")))
        self.assertIn("VERSION ONE", v1)
        self.assertIn("VERSION TWO", v2)

    def test_sub_agents_come_from_the_current_config(self):
        sub = AgentConfig(name="sub_bot", type="llm", model_name="echo",
                          instruction="SUB current", description="the sub",
                          parent_agents='["support_bot"]')
        manager = _manager({"support_bot": [sub]})

        async def _tree():
            async with SnapshotAgent(_snapshot("root from version"), manager=manager) as agent:
                return agent._runner.agent

        root = asyncio.run(_tree())
        self.assertEqual(root.instruction, "root from version")
        self.assertEqual([s.name for s in root.sub_agents], ["sub_bot"])
        self.assertEqual(root.sub_agents[0].instruction, "SUB current")

    def test_an_admin_only_agent_is_not_refused_to_the_eval(self):
        # No roles configured means admin-only; the eval user is not an admin.
        # The eval must run the agent, not collect RBAC's refusal as its answer.
        from shared.callbacks import rbac_callback
        with patch.object(rbac_callback, "get_user_service") as users:
            reply = asyncio.run(self._ask(_snapshot("VERSION ONE", allowed_for_roles=None)))
        self.assertIn("VERSION ONE", reply)
        users.assert_not_called()

    def test_rbac_is_skipped_only_inside_an_eval_run(self):
        from shared.callbacks.rbac_callback import _should_skip_rbac_check
        self.assertFalse(_should_skip_rbac_check("support_bot"))

    def test_the_runtime_picks_the_implementation(self):
        self.assertIsInstance(SnapshotAgent(_snapshot("x")), AdkSnapshotAgent)
        with patch.dict(os.environ, {"AGENT_FRAMEWORK": "langgraph"}):
            self.assertIsInstance(SnapshotAgent(_snapshot("x")), LangGraphSnapshotAgent)

    def test_a_snapshot_without_a_name_is_refused(self):
        with self.assertRaises(ValueError):
            SnapshotAgent({})


class TestLangGraphSnapshotAgent(unittest.TestCase):

    def setUp(self):
        self.children = {}
        patches = [
            patch("shared.utils.langgraph.model_factory.create_chat_model",
                  side_effect=lambda *a, **k: EchoInstructionChatModel()),
            patch("shared.utils.langgraph.agent_builder.AgentBuilder._build_tools",
                  new=AsyncMock(return_value=[])),
            patch("shared.utils.langgraph.agent_builder._load_child_configs",
                  side_effect=lambda name: self.children.get(name, [])),
            patch("shared.utils.langgraph.hooks.get_user_profile_block", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def _ask(self, snapshot):
        async with LangGraphSnapshotAgent(snapshot) as agent:
            return await agent.ask("What are your opening hours?")

    def test_each_version_answers_with_its_own_config(self):
        v1 = asyncio.run(self._ask(_snapshot("VERSION ONE instruction")))
        v2 = asyncio.run(self._ask(_snapshot("VERSION TWO instruction")))
        self.assertIn("VERSION ONE", v1)
        self.assertIn("VERSION TWO", v2)

    def test_sub_agents_come_from_the_current_config(self):
        self.children = {"support_bot": [{
            "name": "sub_bot", "type": "llm", "model_name": "echo", "instruction": "SUB current",
            "description": "the sub", "parent_agents": '["support_bot"]'}]}

        async def _built():
            async with LangGraphSnapshotAgent(_snapshot("root from version")) as agent:
                return agent._built

        built = asyncio.run(_built())
        self.assertEqual(built.config["instruction"], "root from version")
        self.assertEqual(set(built.model_names), {"support_bot", "sub_bot"})

    def test_the_deployed_graph_and_session_tables_are_untouched(self):
        from shared.utils.langgraph.agent_builder import get_agent_builder
        deployed = get_agent_builder()
        with patch("shared.utils.langgraph.agent_builder.get_checkpointer") as checkpointer, \
                patch("shared.utils.langgraph.session_store.get_session_store") as store, \
                patch.object(deployed, "get") as get:
            asyncio.run(self._ask(_snapshot("VERSION ONE")))
        checkpointer.assert_not_called()
        store.assert_not_called()
        get.assert_not_called()
        self.assertEqual(deployed._cache, {})

    def test_an_admin_only_agent_is_not_refused_to_the_eval(self):
        from shared.utils.langgraph import hooks

        async def _ask_and_check():
            async with LangGraphSnapshotAgent(_snapshot("VERSION ONE")) as agent:
                with patch("shared.utils.user_service.get_user_service") as users:
                    # The transfer tool calls check_rbac while the eval runs
                    with patch.object(agent._built, "graph", MagicMock()) as graph:
                        async def _stream(*a, **k):
                            self.assertTrue(is_eval_run())
                            self.assertIsNone(hooks.check_rbac("eval_runner", "support_bot"))
                            return
                            yield
                        graph.astream.side_effect = _stream
                        await agent.ask("hi")
                users.assert_not_called()

        asyncio.run(_ask_and_check())
        self.assertFalse(is_eval_run())

    def test_artifacts_stay_in_memory(self):
        from shared.utils.langgraph.tool_adapter import MateToolContext, get_run_context

        async def _ask_and_list():
            async with LangGraphSnapshotAgent(_snapshot("VERSION ONE")) as agent:
                with patch.object(agent._built, "graph", MagicMock()) as graph:
                    async def _stream(*a, **k):
                        # What a tool such as generate_image does mid-run
                        await MateToolContext(get_run_context()).save_artifact(
                            "chart.png", types.Part.from_text(text="png"))
                        return
                        yield
                    graph.astream.side_effect = _stream
                    await agent.ask("draw it")
                return list(agent._artifacts._service.artifacts)

        with patch("shared.utils.langgraph.artifact_adapter.get_artifact_adapter") as deployed:
            saved = asyncio.run(_ask_and_list())
        deployed.assert_not_called()
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].endswith("/chart.png"), saved)

    def test_a_snapshot_without_a_name_is_refused(self):
        with self.assertRaises(ValueError):
            LangGraphSnapshotAgent({})


class FakeSnapshotAgent:
    """Stands in for SnapshotAgent in endpoint tests and records what it ran."""

    runs = []

    def __init__(self, snapshot, manager=None):
        self.snapshot = snapshot

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def ask(self, input_text, timeout=120.0):
        FakeSnapshotAgent.runs.append((self.snapshot["instruction"], input_text))
        return self.snapshot["instruction"]


class TestEvalEndpoints(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db_client = MagicMock()
        db_client.is_connected.return_value = True
        db_client.get_session.side_effect = lambda: self.Session()

        from shared.utils.dashboard.dashboard_server import DashboardServer
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=db_client):
            server = DashboardServer(app, project_root=PROJECT_ROOT)
        server.db_client = db_client
        server.TestCase, server.EvalResult = TestCase, EvalResult
        server.AgentConfig, server.AgentConfigVersion = AgentConfig, AgentConfigVersion
        app.dependency_overrides[server._get_auth_user_dependency] = lambda: "admin"
        self.client = TestClient(app)

        import json
        session = self.Session()
        agent = AgentConfig(name="support_bot", type="llm", model_name="echo",
                            instruction="VERSION TWO", project_id=1)
        other = AgentConfig(name="other_bot", type="llm", model_name="echo",
                            instruction="OTHER", project_id=1)
        session.add_all([agent, other])
        session.flush()
        v1 = AgentConfigVersion(agent_config_id=agent.id, version_number=1,
                                config_snapshot=json.dumps(_snapshot("VERSION ONE")))
        v2 = AgentConfigVersion(agent_config_id=agent.id, version_number=2,
                                config_snapshot=json.dumps(_snapshot("VERSION TWO")))
        vo = AgentConfigVersion(agent_config_id=other.id, version_number=1,
                                config_snapshot=json.dumps(_snapshot("OTHER", name="other_bot")))
        tc = TestCase(agent_name="support_bot", input="hours?", expected_output="VERSION ONE",
                      eval_method="exact_match")
        session.add_all([v1, v2, vo, tc])
        session.commit()
        self.v1, self.v2, self.vo, self.tc = v1.id, v2.id, vo.id, tc.id
        session.close()

        FakeSnapshotAgent.runs = []
        p = patch("shared.utils.eval_agent_runner.SnapshotAgent", FakeSnapshotAgent)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.engine.dispose)

    def test_single_run_uses_the_selected_version(self):
        resp = self.client.post(f"/dashboard/api/evals/{self.tc}/run", json={"version_id": self.v1})
        self.assertEqual(resp.status_code, 200, resp.text)
        result = resp.json()["result"]
        self.assertEqual(result["actual_output"], "VERSION ONE")
        self.assertEqual(result["version_id"], self.v1)
        self.assertEqual(FakeSnapshotAgent.runs, [("VERSION ONE", "hours?")])

    def test_suite_run_uses_the_selected_version(self):
        resp = self.client.post(f"/dashboard/api/evals/version/{self.v1}/run", json={"results": []})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["passed"], 1)
        self.assertEqual(FakeSnapshotAgent.runs, [("VERSION ONE", "hours?")])

    def test_a_version_of_another_agent_is_refused(self):
        resp = self.client.post(f"/dashboard/api/evals/{self.tc}/run", json={"version_id": self.vo})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(FakeSnapshotAgent.runs, [])

    def test_langgraph_runs_the_selected_version_too(self):
        with patch.dict(os.environ, {"AGENT_FRAMEWORK": "langgraph"}):
            single = self.client.post(f"/dashboard/api/evals/{self.tc}/run",
                                      json={"version_id": self.v1})
            suite = self.client.post(f"/dashboard/api/evals/version/{self.v1}/run",
                                     json={"results": []})
        self.assertEqual((single.status_code, suite.status_code), (200, 200))
        self.assertEqual(FakeSnapshotAgent.runs, [("VERSION ONE", "hours?")] * 2)

    def test_a_supplied_output_needs_no_agent(self):
        resp = self.client.post(f"/dashboard/api/evals/{self.tc}/run",
                                json={"version_id": self.v1, "actual_output": "VERSION ONE"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(FakeSnapshotAgent.runs, [])


if __name__ == "__main__":
    unittest.main()
