"""
require_confirmation on the LangGraph runtime when one turn pauses on several
tool calls.

Each paused call carries the model's tool call id, and each answer resumes only
the call it names: the run carries on once every call has been answered, one
message at a time or all in one.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.langgraph.executor import (_repeats_what_was_sent,
                                             _resume_map)
from shared.utils.langgraph.hitl import (CONFIRMATIONS_KEY, apply_confirmation_wrapping,
                                         confirmation_call_id, pending_confirmations)

CALLS = [
    {"id": "call_1", "name": "create_block", "args": {"label": "b"}},
    {"id": "call_2", "name": "create_block", "args": {"label": "c"}},
]


def _id(call_id: str) -> str:
    """The confirmation call id of one of CALLS."""
    call, = [c for c in CALLS if c["id"] == call_id]
    return confirmation_call_id(call["id"], call["name"], call["args"])


def create_block(label: str) -> str:
    """Create a block."""
    return f"created {label}"


def _graph():
    """
    A react agent whose model asks for both calls at once, then answers "done".

    Built with create_react_agent like agent_builder, which runs each tool call
    as its own task: an answered call finishes and is not run again when its
    sibling is answered later.
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.prebuilt import create_react_agent

    class _Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = _Model(messages=iter([AIMessage(content="", tool_calls=CALLS),
                                  AIMessage(content="done")]))
    tools = apply_confirmation_wrapping([create_block], ["create_block"], agent_name="a")
    graph = create_react_agent(model, tools=tools, checkpointer=InMemorySaver())
    return graph, HumanMessage("create b and c")


async def _answer(graph, config, decisions):
    """Resume as the executor does, draining pauses the decisions already cover."""
    from langgraph.types import Command
    resume_map = await _resume_map(graph, config, {}, decisions)
    out = None
    while resume_map:
        out = await graph.ainvoke(Command(resume=resume_map), config=config)
        resume_map = await _resume_map(graph, config, {}, decisions)
    return out


def _tool_results(state_values):
    return {m.tool_call_id: m.content for m in state_values["messages"] if m.type == "tool"}


class TestParallelConfirmations(unittest.TestCase):

    def test_a_pause_carries_the_models_call_id(self):
        async def run():
            graph, call = _graph()
            config = {"configurable": {"thread_id": "ids"}}
            await graph.ainvoke({"messages": [call]}, config=config)
            pending = pending_confirmations(await graph.aget_state(config))
            self.assertTrue(pending)
            self.assertTrue({p["confirmation_id"] for p in pending}
                            <= {_id("call_1"), _id("call_2")})
        asyncio.run(run())

    def test_answering_one_call_runs_only_that_call(self):
        async def run(thread_id):
            graph, call = _graph()
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke({"messages": [call]}, config=config)
            await _answer(graph, config, {_id("call_2"): True})
            state = await graph.aget_state(config)
            self.assertNotIn("call_1", _tool_results(state.values))
            self.assertEqual([p["confirmation_id"] for p in pending_confirmations(state)],
                             [_id("call_1")])

            await _answer(graph, config, {_id("call_1"): False})
            state = await graph.aget_state(config)
            self.assertEqual(pending_confirmations(state), [])
            results = _tool_results(state.values)
            self.assertEqual(results["call_2"], "created c")
            self.assertIn("rejected", results["call_1"])
            self.assertEqual(state.values["messages"][-1].content, "done")

        for attempt in range(3):
            asyncio.run(run(f"one-{attempt}"))

    def test_answering_both_in_one_message(self):
        async def run(thread_id):
            graph, call = _graph()
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke({"messages": [call]}, config=config)
            await _answer(graph, config, {_id("call_1"): True,
                                          _id("call_2"): False})
            state = await graph.aget_state(config)
            self.assertEqual(pending_confirmations(state), [])
            results = _tool_results(state.values)
            self.assertEqual(results["call_1"], "created b")
            self.assertIn("rejected", results["call_2"])

        for attempt in range(3):
            asyncio.run(run(f"both-{attempt}"))

    def test_a_decision_nothing_waits_for_resumes_nothing(self):
        async def run():
            graph, call = _graph()
            config = {"configurable": {"thread_id": "stale"}}
            await graph.ainvoke({"messages": [call]}, config=config)
            self.assertEqual(await _resume_map(graph, config, {}, {"adk-confirm-nothing": True}), {})
        asyncio.run(run())

    def test_an_approval_does_not_run_another_call_with_the_same_id(self):
        async def run():
            graph, call = _graph()
            config = {"configurable": {"thread_id": "same-id"}}
            await graph.ainvoke({"messages": [call]}, config=config)
            other = confirmation_call_id("call_1", "create_block", {"label": "something else"})
            self.assertNotEqual(other, _id("call_1"))
            self.assertEqual(await _resume_map(graph, config, {}, {other: True}), {})
        asyncio.run(run())

    def test_the_model_never_sees_the_injected_call_id(self):
        from langchain_core.tools import tool as to_tool
        wrapped = apply_confirmation_wrapping([create_block], ["create_block"])[0]
        self.assertEqual(set(to_tool(wrapped).tool_call_schema.model_json_schema()["properties"]),
                         {"label"})


class TestRepeatedPause(unittest.TestCase):

    def _event(self, call_id):
        return {"content": {"parts": [{"functionCall": {
            "id": call_id, "name": "adk_request_confirmation", "args": {}}}]}}

    def test_a_pause_the_caller_already_has_is_dropped(self):
        self.assertTrue(_repeats_what_was_sent(self._event("adk-confirm-a"),
                                                        {"adk-confirm-a"}))

    def test_a_new_pause_is_kept(self):
        self.assertFalse(_repeats_what_was_sent(self._event("adk-confirm-b"),
                                                         {"adk-confirm-a"}))

    def test_a_result_the_caller_already_has_is_dropped(self):
        result = {"content": {"parts": [{"functionResponse": {
            "id": "call_1", "name": "create_block", "response": {"result": "created b"}}}]}}
        self.assertTrue(_repeats_what_was_sent(result, {"call_1"}))
        self.assertFalse(_repeats_what_was_sent(result, {"call_2"}))

    def test_text_is_kept(self):
        text = {"content": {"parts": [{"text": "done"}]}}
        self.assertFalse(_repeats_what_was_sent(text, {"adk-confirm-a"}))
        mixed = {"content": {"parts": [{"text": "done"}, {"functionResponse": {"id": "call_1"}}]}}
        self.assertFalse(_repeats_what_was_sent(mixed, {"call_1"}))


class TestResumeValue(unittest.TestCase):

    def test_every_answered_pause_gets_the_whole_map(self):
        class _I:
            def __init__(self, id_, call_id):
                self.id = id_
                self.value = {"confirmationId": _id(call_id), "originalFunctionCall": {"id": call_id}}

        class _T:
            def __init__(self, interrupts):
                self.interrupts = interrupts

        class _S:
            tasks = [_T([_I("int-1", "call_1")]), _T([_I("int-2", "call_2")])]

        class _G:
            async def aget_state(self, config):
                return _S()

        decisions = {_id("call_1"): True}
        self.assertEqual(asyncio.run(_resume_map(_G(), {}, {}, decisions)),
                         {"int-1": {CONFIRMATIONS_KEY: decisions}})


if __name__ == "__main__":
    unittest.main()
