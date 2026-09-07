#!/usr/bin/env python3
"""
A subagent may not exceed the tools its parent holds.

Reported privately against 695c735. `_build_subagent_tools` equipped a child with
any tool family named in a subtask, defaulting to `True` when the parent had no
such entry — so an orchestrator's prompt decided the child's privileges, not the
parent's configuration.

Three things made that serious rather than untidy:

* `code_executor` is not in the forbidden set (only delegation tools are, to stop
  fork bombs), and `{"code_executor": true}` alone is enough to build
  `execute_shell_command`, which runs as the server user.
* The widget guard from #72 keys off the agent name, and a subagent is named
  `subagent_<uuid>`, which has no widget key — so the refusal that exists to keep
  anonymous visitors away from a shell did not apply to the child.
* A subagent is not a row in `agents_config`, so it carries no RBAC of its own.
  The parent's tool set is the only boundary there is.

The route in is prompt injection: anyone who can prompt an agent that has
delegation enabled could ask for a child with a shell.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.tools.subagent_delegation_tool import _build_subagent_tools


def tool_names(tools):
    return {getattr(t, "__name__", getattr(t, "name", str(t))) for t in tools}


def parent(**tool_config):
    return {
        "name": "orchestrator_agent",
        "model_name": "gemini-2.5-flash",
        "project_id": 1,
        "tool_config": json.dumps(tool_config) if tool_config else None,
    }


class TestChildCannotExceedParent(unittest.TestCase):

    def setUp(self):
        # Default: nothing is widget-exposed, so only the parent's config decides.
        self._guard = patch(
            "shared.utils.tools.tool_factory.ToolFactory._agent_has_widget_key",
            return_value=False)
        self._guard.start()
        self.addCleanup(self._guard.stop)

    def test_a_parent_without_the_executor_cannot_hand_one_to_a_child(self):
        tools, assigned = _build_subagent_tools(
            ["google_search", "code_executor"], parent(google_search=True))
        self.assertNotIn("code_executor", assigned)
        self.assertNotIn("execute_shell_command", tool_names(tools))
        self.assertNotIn("execute_python_code", tool_names(tools))

    def test_the_aliases_are_closed_too(self):
        # bash/terminal/python/code all canonicalise to code_executor, so blocking
        # only the literal name would leave four ways round it.
        for alias in ("bash", "terminal", "python", "code"):
            with self.subTest(alias=alias):
                tools, assigned = _build_subagent_tools([alias], parent(google_search=True))
                self.assertNotIn("code_executor", assigned)
                self.assertNotIn("execute_shell_command", tool_names(tools))

    def test_agent_management_is_not_granted_to_a_child(self):
        # create_agent pulls delete_agent, update_agent and read_agent with it.
        tools, assigned = _build_subagent_tools(["create_agent"], parent(google_search=True))
        self.assertNotIn("create_agent", assigned)
        self.assertFalse(
            tool_names(tools) & {"create_agent", "delete_agent", "update_agent"})

    def test_shop_is_not_granted_to_a_child(self):
        tools, assigned = _build_subagent_tools(["shop"], parent(google_search=True))
        self.assertNotIn("shop", assigned)
        self.assertNotIn("place_order", tool_names(tools))

    def test_mcp_is_not_granted_when_the_parent_has_no_servers(self):
        _, assigned = _build_subagent_tools(["mcp"], parent(google_search=True))
        self.assertNotIn("mcp", assigned)

    def test_an_unknown_tool_name_is_not_silently_granted(self):
        _, assigned = _build_subagent_tools(["totally_made_up"], parent(google_search=True))
        self.assertEqual(assigned, [])


class TestWidgetExposureIsInherited(unittest.TestCase):
    """The parent is the reachable surface, so its exposure is the child's."""

    def test_a_widget_exposed_parent_cannot_launder_the_executor_through_a_child(self):
        with patch("shared.utils.tools.tool_factory.ToolFactory._agent_has_widget_key",
                   return_value=True):
            tools, assigned = _build_subagent_tools(
                ["code_executor"], parent(code_executor=True))
        self.assertNotIn("code_executor", assigned)
        self.assertNotIn("execute_shell_command", tool_names(tools))

    def test_a_parent_that_legitimately_holds_the_executor_still_passes_it_on(self):
        # The fix must not break the supported case.
        with patch("shared.utils.tools.tool_factory.ToolFactory._agent_has_widget_key",
                   return_value=False):
            tools, assigned = _build_subagent_tools(
                ["code_executor"], parent(code_executor=True))
        self.assertIn("code_executor", assigned)
        self.assertIn("execute_shell_command", tool_names(tools))


class TestExistingBehaviourSurvives(unittest.TestCase):

    def setUp(self):
        self._guard = patch(
            "shared.utils.tools.tool_factory.ToolFactory._agent_has_widget_key",
            return_value=False)
        self._guard.start()
        self.addCleanup(self._guard.stop)

    def test_tools_the_parent_holds_are_still_granted(self):
        _, assigned = _build_subagent_tools(
            ["search", "memory"], parent(google_search=True, memory_blocks={"blocks": ["notes"]}))
        self.assertIn("google_search", assigned)
        self.assertIn("memory_blocks", assigned)

    def test_the_parents_own_settings_are_inherited_not_flattened(self):
        # A child that got `True` instead of the parent's dict would lose the
        # catalog and quietly behave differently.
        captured = {}
        real = json.dumps

        def capture(obj, *a, **k):
            if isinstance(obj, dict) and "memory_blocks" in obj:
                captured.update(obj)
            return real(obj, *a, **k)

        with patch("shared.utils.tools.subagent_delegation_tool.json.dumps", side_effect=capture):
            _build_subagent_tools(
                ["memory"], parent(memory_blocks={"blocks": ["project_notes"]}))
        self.assertEqual(captured.get("memory_blocks"), {"blocks": ["project_notes"]})

    def test_delegation_tools_are_still_stripped(self):
        _, assigned = _build_subagent_tools(
            ["subagent_delegation", "delegate_subtasks", "google_search"],
            parent(google_search=True, subagent_delegation={"max_subagents": 3}))
        self.assertNotIn("subagent_delegation", assigned)
        self.assertNotIn("delegate_subtasks", assigned)
        self.assertIn("google_search", assigned)


if __name__ == "__main__":
    unittest.main()
