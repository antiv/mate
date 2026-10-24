#!/usr/bin/env python3
"""
LangGraph re-checks the role of the agent a turn resumes in (1.6.0 item 7).

A multi-agent graph starts each turn at the checkpointed current_agent, the
sub-agent an earlier turn was transferred to. The run checked only the agent
requested, so a user whose roles allowed the root kept talking to a sub-agent
they had no role for, or no longer had: after an admin took the role away, or
after the sub-agent's allowed roles changed. ADK checks the agent that answers on
every model call. The executor now checks the agent it resumes in too.

The transfer tool checked roles only when it had a run context, and let the
transfer through without one. It now refuses, except in a graph built for
LangGraph Studio, which has no MATE user.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = {"name": "root", "type": "llm", "model_name": "openai/x", "instruction": "route"}
CHILD = {"name": "child", "type": "llm", "model_name": "openai/x", "instruction": "help",
         "parent_agents": '["root"]'}


# The agents whose node ran, in order
RAN = []


def _node(config):
    from langchain_core.messages import AIMessage

    def answer(state):
        RAN.append(config["name"])
        return {"messages": [AIMessage(content=f"answer from {config['name']}")]}
    return answer


async def _build():
    from langgraph.checkpoint.memory import InMemorySaver
    from shared.utils.langgraph.agent_builder import AgentBuilder

    async def react_agent(self, config, children_of, **kwargs):
        return _node(config)

    with patch("shared.utils.langgraph.agent_builder._load_child_configs",
               side_effect=lambda name: [CHILD] if name == "root" else []), \
            patch.object(AgentBuilder, "_build_react_agent", react_agent), \
            patch.object(AgentBuilder, "_build_guardrail_engines", return_value={}):
        return await AgentBuilder().build_for_config(dict(ROOT), InMemorySaver())


def _denial(agent_name):
    return {"error_code": "RBAC_ACCESS_DENIED", "error_message": f"denied {agent_name}",
            "author": agent_name}


class TestResumedAgentIsChecked(unittest.TestCase):

    def setUp(self):
        RAN.clear()

    def _run(self, built, allowed, session_id="s1"):
        from shared.utils.langgraph import executor
        checked = []

        def check_rbac(user_id, agent_name, session_id=None):
            checked.append(agent_name)
            return None if agent_name in allowed else _denial(agent_name)

        builder = MagicMock()

        async def get(app_name, client_tools=None):
            return built
        builder.get = get

        async def collect():
            events = []
            async for event in executor.execute_run(
                    "root", "u1", session_id, {"role": "user", "parts": [{"text": "hi"}]}, "inv-1"):
                events.append(event)
            return events

        with patch.object(executor, "get_agent_builder", return_value=builder), \
                patch.object(executor, "get_session_store", return_value=MagicMock()), \
                patch("shared.utils.langgraph.hooks.check_rbac", side_effect=check_rbac), \
                patch("shared.utils.langgraph.hooks.check_input_guardrails", return_value=None), \
                patch.object(executor, "_log_token_usage"):
            return asyncio.run(collect()), checked

    def _transferred(self, built, session_id="s1"):
        config = {"configurable": {"thread_id": session_id}}
        asyncio.run(built.graph.aupdate_state(config, {"current_agent": "child"}, as_node="root"))

    def test_a_user_without_the_sub_agents_role_is_refused_after_a_transfer(self):
        built = asyncio.run(_build())
        self._transferred(built)
        events, checked = self._run(built, allowed={"root"})
        self.assertEqual(checked, ["root", "child"])
        self.assertEqual(events[0]["error_code"], "RBAC_ACCESS_DENIED")
        self.assertEqual(events[0]["invocationId"], "inv-1")
        self.assertEqual(RAN, [])

    def test_a_user_with_both_roles_goes_on_in_the_sub_agent(self):
        built = asyncio.run(_build())
        self._transferred(built)
        events, checked = self._run(built, allowed={"root", "child"})
        self.assertEqual(checked, ["root", "child"])
        self.assertEqual(RAN, ["child"])

    def test_a_turn_at_the_root_is_checked_once(self):
        built = asyncio.run(_build())
        events, checked = self._run(built, allowed={"root"})
        self.assertEqual(checked, ["root"])
        self.assertEqual(RAN, ["root"])

    def test_a_checkpoint_that_cannot_be_read_refuses(self):
        built = asyncio.run(_build())

        async def broken(config):
            raise RuntimeError("checkpointer down")
        built.graph.aget_state = broken
        with self.assertLogs("shared.utils.langgraph.executor", "ERROR"):
            events, _ = self._run(built, allowed={"root", "child"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["error_code"], "RBAC_ACCESS_DENIED")
        self.assertEqual(RAN, [])

    def test_a_current_agent_outside_the_tree_starts_at_the_root(self):
        from shared.utils.langgraph.executor import _resumed_agent
        built = asyncio.run(_build())
        config = {"configurable": {"thread_id": "s2"}}
        asyncio.run(built.graph.aupdate_state(config, {"current_agent": "elsewhere"}, as_node="root"))
        self.assertEqual(asyncio.run(_resumed_agent(built, config)), "root")


class TestTransferWithoutRunContext(unittest.TestCase):

    def test_a_transfer_without_a_run_context_is_refused(self):
        from shared.utils.langgraph.agent_builder import _make_handoff_tool
        tool = _make_handoff_tool("root", {"child": "helper"})
        with patch("shared.utils.langgraph.hooks.check_rbac_message") as check, \
                self.assertLogs("shared.utils.langgraph.agent_builder", "ERROR"):
            result = tool.func(agent_name="child", tool_call_id="tc1")
        self.assertIsInstance(result, str)
        self.assertIn("refused", result)
        check.assert_not_called()

    def test_a_studio_graph_transfers_without_one(self):
        from langgraph.types import Command
        from shared.utils.langgraph.agent_builder import _make_handoff_tool
        tool = _make_handoff_tool("root", {"child": "helper"}, require_run_context=False)
        self.assertIsInstance(tool.func(agent_name="child", tool_call_id="tc1"), Command)

    def test_the_studio_builder_is_the_only_one_without_the_requirement(self):
        from shared.utils.langgraph.agent_builder import AgentBuilder, get_agent_builder
        self.assertTrue(AgentBuilder()._require_run_context)
        self.assertTrue(get_agent_builder()._require_run_context)
        import inspect
        from shared.utils.langgraph import studio_graph
        self.assertIn("AgentBuilder(require_run_context=False)", inspect.getsource(studio_graph))


if __name__ == "__main__":
    unittest.main()
