#!/usr/bin/env python3
"""
The LangGraph side of caller-declared tools.

Here a client tool is a real StructuredTool whose body only interrupts, so the
graph pauses where ADK would emit a long-running call. The resume has to be
addressed by interrupt id, so the mapping from the tool call id the caller
answers back to that interrupt is what these guard.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.langgraph.client_tools import (CLIENT_TOOL_INTERRUPT_KEY,
                                                 build_client_tools,
                                                 client_tools_key,
                                                 pending_client_calls)
from shared.utils.langgraph.hitl import (CONFIRMATION_RESPONSE_NAME,
                                         extract_client_tool_responses,
                                         extract_confirmation_response)

READ = {
    "name": "read",
    "description": "Read a file",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]},
}
BASH = {"name": "bash", "description": "Run a command",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}


class _Interrupt:
    def __init__(self, id_, value):
        self.id = id_
        self.value = value


class _Task:
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _State:
    def __init__(self, tasks):
        self.tasks = tasks


class TestCacheKey(unittest.TestCase):
    """
    Compiled graphs are cached per agent. Without the tools in the key, the
    first caller's tool set would be served to every later caller.
    """

    def test_no_tools_is_the_empty_key(self):
        self.assertEqual(client_tools_key(None), "")
        self.assertEqual(client_tools_key([]), "")

    def test_different_tool_sets_get_different_keys(self):
        self.assertNotEqual(client_tools_key([READ]), client_tools_key([READ, BASH]))

    def test_the_key_ignores_declaration_order(self):
        self.assertEqual(client_tools_key([READ, BASH]), client_tools_key([BASH, READ]))

    def test_a_changed_schema_changes_the_key(self):
        changed = dict(READ, parameters={"type": "object",
                                         "properties": {"path": {"type": "string"},
                                                        "limit": {"type": "integer"}}})
        self.assertNotEqual(client_tools_key([READ]), client_tools_key([changed]))


class TestToolConstruction(unittest.TestCase):

    def test_the_callers_schema_becomes_the_tool_arguments(self):
        tool = build_client_tools([READ])[0]
        self.assertEqual(tool.name, "read")
        self.assertIn("path", tool.args)

    def test_the_injected_call_id_is_hidden_from_the_model(self):
        # It is how the pause is matched to the caller's answer, not something
        # the model should be asked to supply.
        tool = build_client_tools([READ])[0]
        self.assertNotIn("tool_call_id", tool.args)

    def test_the_agents_own_tool_wins_a_name_collision(self):
        self.assertEqual(build_client_tools([READ], reserved_names={"read"}), [])

    def test_no_declarations_build_nothing(self):
        self.assertEqual(build_client_tools(None), [])


class TestPendingCalls(unittest.TestCase):

    def test_a_paused_client_call_is_reported_with_its_interrupt_id(self):
        state = _State([_Task([_Interrupt("int-1", {
            CLIENT_TOOL_INTERRUPT_KEY: {"id": "call-1", "name": "read",
                                        "args": {"path": "a.py"}}})])])
        self.assertEqual(pending_client_calls(state), [{
            "interrupt_id": "int-1", "id": "call-1", "name": "read",
            "args": {"path": "a.py"},
        }])

    def test_parallel_calls_are_all_reported(self):
        state = _State([
            _Task([_Interrupt("int-1", {CLIENT_TOOL_INTERRUPT_KEY: {"id": "c1", "name": "read"}})]),
            _Task([_Interrupt("int-2", {CLIENT_TOOL_INTERRUPT_KEY: {"id": "c2", "name": "bash"}})]),
        ])
        self.assertEqual([c["id"] for c in pending_client_calls(state)], ["c1", "c2"])

    def test_a_confirmation_pause_is_not_a_client_call(self):
        state = _State([_Task([_Interrupt("int-1", {"toolConfirmation": {"confirmed": False}})])])
        self.assertEqual(pending_client_calls(state), [])

    def test_an_unpaused_state_has_nothing_pending(self):
        self.assertEqual(pending_client_calls(_State([])), [])


class TestResponseExtraction(unittest.TestCase):

    def test_a_tool_result_is_read_in_either_casing(self):
        for key in ("function_response", "functionResponse"):
            message = {"parts": [{key: {"id": "c1", "name": "read",
                                        "response": {"result": "body"}}}]}
            self.assertEqual(extract_client_tool_responses(message),
                             [{"id": "c1", "name": "read", "result": "body"}])

    def test_a_confirmation_answer_is_not_mistaken_for_a_tool_result(self):
        message = {"parts": [{"function_response": {
            "id": "c1", "name": CONFIRMATION_RESPONSE_NAME,
            "response": {"confirmed": True}}}]}
        self.assertEqual(extract_client_tool_responses(message), [])
        self.assertTrue(extract_confirmation_response(message))

    def test_parallel_results_keep_their_order(self):
        message = {"parts": [
            {"functionResponse": {"id": "c1", "name": "read", "response": {"result": "a"}}},
            {"functionResponse": {"id": "c2", "name": "bash", "response": {"result": "b"}}},
        ]}
        self.assertEqual([r["id"] for r in extract_client_tool_responses(message)],
                         ["c1", "c2"])

    def test_a_plain_text_message_carries_no_results(self):
        self.assertEqual(extract_client_tool_responses({"parts": [{"text": "hi"}]}), [])


class TestParallelCallsDrainInOneRoundTrip(unittest.TestCase):
    """
    A tool node surfaces one pause at a time, so two client tools in one
    assistant turn pause twice. The caller answers both at once, so the executor
    has to drain the second pause itself — otherwise it would ask again for a
    result it already has.

    The node runs its calls in parallel threads and LangGraph hands resume
    values out by call order, so which call draws a value is a coin toss: the
    result has to reach the call it was meant for whichever way the toss goes.
    """

    def _graph(self):
        from langchain_core.messages import AIMessage
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        tools = build_client_tools([READ, BASH])
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        call = AIMessage(content="", tool_calls=[
            {"id": "call_1", "name": "read", "args": {"path": "a.py"}},
            {"id": "call_2", "name": "bash", "args": {"cmd": "ls"}},
        ])
        return graph, call

    def test_both_results_are_delivered(self):
        import asyncio

        from langgraph.types import Command

        from shared.utils.langgraph.executor import _resume_map

        graph, call = self._graph()

        async def run(thread_id):
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke({"messages": [call]}, config=config)
            answers = {"call_1": "import os", "call_2": "a.py b.py"}
            resume_map = await _resume_map(graph, config, answers)
            self.assertTrue(resume_map)
            graph_input = Command(resume=resume_map)
            for _ in range(10):
                out = await graph.ainvoke(graph_input, config=config)
                resume_map = await _resume_map(graph, config, answers)
                if not resume_map:
                    return out
                graph_input = Command(resume=resume_map)
            self.fail("the resume loop did not end")

        # Repeated because the misdelivery this guards against showed on about
        # half of the runs.
        for attempt in range(12):
            out = asyncio.run(run(f"t-{attempt}"))
            results = {m.tool_call_id: m.content for m in out["messages"] if m.type == "tool"}
            self.assertEqual(results, {"call_1": "import os", "call_2": "a.py b.py"})

    def test_a_result_nothing_is_waiting_for_ends_the_loop(self):
        import asyncio

        from shared.utils.langgraph.executor import _resume_map

        graph, _ = self._graph()
        config = {"configurable": {"thread_id": "t-idle"}}
        self.assertEqual(asyncio.run(_resume_map(graph, config, {"call_9": "x"})), {})


if __name__ == "__main__":
    unittest.main()
