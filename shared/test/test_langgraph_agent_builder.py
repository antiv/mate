#!/usr/bin/env python3
"""
Unit tests for the LangGraph agent builder: tree collection, transfer targets,
handoff tool behavior, unsupported types, and HITL confirmation wrapping.
"""

import asyncio
import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.langgraph.agent_builder import (
    AgentBuilder,
    AgentNotFoundError,
    UnsupportedAgentTypeError,
    _build_transfer_note,
    _make_handoff_tool,
)
from shared.utils.langgraph.hitl import (
    apply_confirmation_wrapping,
    extract_confirmation_response,
)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _config(name, agent_type="llm", parents=None, description=""):
    return {
        "name": name, "type": agent_type, "model_name": "openai/gpt-test",
        "description": description, "instruction": f"You are {name}.",
        "tool_config": None, "mcp_servers_config": None,
        "generate_content_config": None, "planner_config": None,
        "guardrail_config": None, "parent_agents": parents or [],
    }


class TestTreeCollection(unittest.TestCase):

    def _builder_with_configs(self, configs):
        builder = AgentBuilder()
        by_name = {c["name"]: c for c in configs}

        def load_config(name):
            return by_name.get(name)

        def load_children(parent):
            return [c for c in configs if parent in (c.get("parent_agents") or [])]

        return builder, load_config, load_children

    def test_collect_tree_with_children(self):
        configs = [
            _config("root"),
            _config("child_a", parents=["root"], description="does A"),
            _config("child_b", parents=["root"]),
            _config("grandchild", parents=["child_a"]),
        ]
        builder, load_config, load_children = self._builder_with_configs(configs)
        tree, children_of = {}, {}
        with patch("shared.utils.langgraph.agent_builder._load_child_configs", side_effect=load_children):
            builder._collect_tree(configs[0], tree, children_of)
        self.assertEqual(set(tree), {"root", "child_a", "child_b", "grandchild"})
        self.assertEqual(children_of["root"], ["child_a", "child_b"])
        self.assertEqual(children_of["child_a"], ["grandchild"])

    def test_cycle_does_not_recurse_forever(self):
        configs = [
            _config("a", parents=["b"]),
            _config("b", parents=["a"]),
        ]
        builder, _, load_children = self._builder_with_configs(configs)
        tree, children_of = {}, {}
        with patch("shared.utils.langgraph.agent_builder._load_child_configs", side_effect=load_children):
            builder._collect_tree(configs[0], tree, children_of)
        self.assertEqual(set(tree), {"a", "b"})

    def test_non_llm_children_are_skipped(self):
        configs = [
            _config("root"),
            _config("workflow_child", agent_type="graph", parents=["root"]),
        ]
        builder, _, load_children = self._builder_with_configs(configs)
        tree, children_of = {}, {}
        with patch("shared.utils.langgraph.agent_builder._load_child_configs", side_effect=load_children):
            builder._collect_tree(configs[0], tree, children_of)
        self.assertEqual(set(tree), {"root"})
        self.assertEqual(children_of["root"], [])

    def test_transfer_targets_include_children_and_parents(self):
        builder = AgentBuilder()
        tree = {"root": _config("root", description="the boss"),
                "child": _config("child", description="the helper")}
        children_of = {"root": ["child"], "child": []}
        self.assertEqual(builder._transfer_targets("root", tree, children_of),
                         {"child": "the helper"})
        self.assertEqual(builder._transfer_targets("child", tree, children_of),
                         {"root": "the boss"})


class TestBuildErrors(unittest.TestCase):

    def test_unknown_agent_raises(self):
        builder = AgentBuilder()
        with patch("shared.utils.langgraph.agent_builder._load_agent_config", return_value=None):
            with self.assertRaises(AgentNotFoundError):
                _run(builder._build("ghost"))

    def test_workflow_type_raises_unsupported(self):
        builder = AgentBuilder()
        config = _config("wf", agent_type="graph")
        with patch("shared.utils.langgraph.agent_builder._load_agent_config", return_value=config):
            with self.assertRaises(UnsupportedAgentTypeError) as ctx:
                _run(builder._build("wf"))
        self.assertEqual(ctx.exception.agent_type, "graph")


class TestTransferNote(unittest.TestCase):
    """The injected transfer instructions mirror ADK's wording so routing is reliable."""

    def _tree(self):
        tree = {
            "root": _config("root", description="the boss"),
            "child_a": _config("child_a", parents=["root"], description="opening expert"),
            "child_b": _config("child_b", parents=["root"], description="history expert"),
        }
        children_of = {"root": ["child_a", "child_b"], "child_a": [], "child_b": []}
        return tree, children_of

    def test_root_note_lists_children_and_routing_rule(self):
        tree, children_of = self._tree()
        note = _build_transfer_note("root", tree, children_of)
        self.assertIn("Agent name: child_a", note)
        self.assertIn("Agent description: opening expert", note)
        self.assertIn("Agent name: child_b", note)
        # the ADK rule that makes weak models actually call the tool
        self.assertIn("do not generate any text other than the function call", note)
        self.assertIn("`child_a`, `child_b`", note)
        # root has no parent — no back-transfer line
        self.assertNotIn("parent agent", note)

    def test_child_note_has_parent_line(self):
        tree, children_of = self._tree()
        note = _build_transfer_note("child_a", tree, children_of)
        self.assertIn("transfer to your parent agent root", note)
        self.assertIn("`root`", note)
        # child has no children of its own — no agent list section
        self.assertNotIn("You have a list of other agents", note)

    def test_agent_without_targets_gets_no_note(self):
        tree = {"solo": _config("solo")}
        self.assertIsNone(_build_transfer_note("solo", tree, {"solo": []}))


class TestHandoffTool(unittest.TestCase):

    def test_rejects_unknown_target(self):
        tool = _make_handoff_tool("root", {"child": "helper"})
        result = tool.func(agent_name="stranger", tool_call_id="tc1")
        self.assertIn("Unknown agent", result)

    def test_transfer_returns_parent_command(self):
        from langgraph.types import Command
        tool = _make_handoff_tool("root", {"child": "helper"})
        with patch("shared.utils.langgraph.hooks.check_rbac_message", return_value=None):
            result = tool.func(agent_name="child", tool_call_id="tc1")
        self.assertIsInstance(result, Command)
        self.assertEqual(result.goto, "child")
        self.assertEqual(result.graph, Command.PARENT)
        self.assertEqual(result.update["current_agent"], "child")
        tool_msg = result.update["messages"][0]
        self.assertEqual(tool_msg.tool_call_id, "tc1")

    def test_transfer_denied_by_rbac(self):
        from shared.utils.langgraph.tool_adapter import RunContext, reset_run_context, set_run_context
        tool = _make_handoff_tool("root", {"child": "helper"})
        token = set_run_context(RunContext(app_name="root", user_id="u1",
                                           session_id="s1", agent_name="root"))
        try:
            with patch("shared.utils.langgraph.hooks.check_rbac_message",
                       return_value="🚫 Access Denied"):
                result = tool.func(agent_name="child", tool_call_id="tc1")
        finally:
            reset_run_context(token)
        self.assertEqual(result, "🚫 Access Denied")

    def test_tool_description_lists_targets(self):
        tool = _make_handoff_tool("root", {"child": "the helper"})
        self.assertIn("child", tool.description)
        self.assertIn("the helper", tool.description)


class TestHitl(unittest.TestCase):

    def test_only_named_tools_are_wrapped(self):
        def safe_tool(x: int) -> int:
            return x

        def dangerous_tool(x: int) -> int:
            return -x

        wrapped = apply_confirmation_wrapping([safe_tool, dangerous_tool],
                                              ["dangerous_tool"], agent_name="a")
        self.assertIs(wrapped[0], safe_tool)
        self.assertIsNot(wrapped[1], dangerous_tool)
        self.assertEqual(wrapped[1].__name__, "dangerous_tool")

    def test_extract_confirmation_response(self):
        self.assertIsNone(extract_confirmation_response({"parts": [{"text": "hi"}]}))
        approve = {"parts": [{"function_response": {
            "id": "c1", "name": "adk_request_confirmation", "response": {"confirmed": True}}}]}
        reject = {"parts": [{"functionResponse": {
            "id": "c1", "name": "adk_request_confirmation", "response": {"confirmed": False}}}]}
        self.assertIs(extract_confirmation_response(approve), True)
        self.assertIs(extract_confirmation_response(reject), False)


if __name__ == "__main__":
    unittest.main()
