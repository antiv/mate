#!/usr/bin/env python3
"""
Unit tests for the LangGraph tool adaptation layer (MateToolContext shim,
tool_context signature stripping, run-context injection, state deltas).
"""

import asyncio
import inspect
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.langgraph.tool_adapter import (
    MateState,
    MateToolContext,
    RunContext,
    adapt_adk_tool,
    adapt_tools,
    reset_run_context,
    set_run_context,
)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _make_run_context(**overrides):
    defaults = dict(app_name="my_app", user_id="u1", session_id="s1", agent_name="my_agent")
    defaults.update(overrides)
    return RunContext(**defaults)


class TestMateState(unittest.TestCase):

    def test_records_writes_as_delta(self):
        state = MateState({"existing": 1})
        state["cart"] = ["item"]
        state.update(other=2)
        self.assertEqual(state["existing"], 1)
        self.assertEqual(state.pop_delta(), {"cart": ["item"], "other": 2})
        self.assertEqual(state.pop_delta(), {})

    def test_reads_do_not_create_delta(self):
        state = MateState({"a": 1})
        _ = state.get("a")
        _ = state.get("missing")
        self.assertEqual(state.pop_delta(), {})


class TestMateToolContext(unittest.TestCase):

    def test_duck_typed_surface(self):
        ctx = MateToolContext(_make_run_context())
        # the attribute chains the 17 tool files actually use
        self.assertEqual(ctx.user_id, "u1")
        self.assertEqual(ctx.agent_name, "my_agent")
        self.assertEqual(ctx.session.id, "s1")
        self.assertEqual(ctx.session.user_id, "u1")
        self.assertEqual(ctx._invocation_context.user_id, "u1")
        self.assertEqual(ctx._invocation_context.app_name, "my_app")
        self.assertEqual(ctx._invocation_context.session.id, "s1")
        self.assertEqual(ctx._invocation_context.agent.name, "my_agent")

    def test_state_shared_with_run_context(self):
        run_context = _make_run_context()
        ctx = MateToolContext(run_context)
        ctx.state["key"] = "value"
        self.assertEqual(run_context.pop_state_delta(), {"key": "value"})

    def test_unknown_attribute_raises(self):
        ctx = MateToolContext(_make_run_context())
        with self.assertRaises(AttributeError):
            _ = ctx.definitely_not_a_real_attribute


class TestAdaptAdkTool(unittest.TestCase):

    def test_signature_excludes_tool_context(self):
        def my_tool(city: str, tool_context=None) -> str:
            """Docstring stays."""
            return city

        adapted = adapt_adk_tool(my_tool)
        params = list(inspect.signature(adapted).parameters)
        self.assertEqual(params, ["city"])
        self.assertNotIn("tool_context", adapted.__annotations__)
        self.assertEqual(adapted.__name__, "my_tool")
        self.assertEqual(adapted.__doc__, "Docstring stays.")

    def test_context_injected_at_call_time(self):
        captured = {}

        def my_tool(x: int, tool_context=None) -> int:
            captured["ctx"] = tool_context
            return x * 2

        adapted = adapt_adk_tool(my_tool)
        run_context = _make_run_context()
        token = set_run_context(run_context)
        try:
            result = _run(adapted(x=21))
        finally:
            reset_run_context(token)
        self.assertEqual(result, 42)
        self.assertIsInstance(captured["ctx"], MateToolContext)
        self.assertEqual(captured["ctx"].session.id, "s1")

    def test_async_tool_supported(self):
        async def my_async_tool(name: str, tool_context=None) -> str:
            return f"hi {name}"

        adapted = adapt_adk_tool(my_async_tool)
        token = set_run_context(_make_run_context())
        try:
            self.assertEqual(_run(adapted(name="ana")), "hi ana")
        finally:
            reset_run_context(token)

    def test_tool_without_context_param_unchanged(self):
        def plain_tool(a: int) -> int:
            return a

        self.assertIs(adapt_adk_tool(plain_tool), plain_tool)

    def test_no_run_context_passes_none(self):
        captured = {}

        def my_tool(tool_context=None) -> str:
            captured["ctx"] = tool_context
            return "ok"

        adapted = adapt_adk_tool(my_tool)
        self.assertEqual(_run(adapted()), "ok")
        self.assertIsNone(captured["ctx"])


class TestAdaptTools(unittest.TestCase):

    def test_skips_adk_tool_objects(self):
        from google.adk.tools.function_tool import FunctionTool

        def fn(tool_context=None) -> str:
            return "x"

        adapted = adapt_tools([fn, FunctionTool(fn), "not-callable"])
        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0].__name__, "fn")


class TestRunContextDeltas(unittest.TestCase):

    def test_artifact_delta_collected(self):
        run_context = _make_run_context()
        run_context.artifact_delta["img.png"] = 3
        self.assertEqual(run_context.pop_artifact_delta(), {"img.png": 3})
        self.assertEqual(run_context.pop_artifact_delta(), {})


if __name__ == "__main__":
    unittest.main()
