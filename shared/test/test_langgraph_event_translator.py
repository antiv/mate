#!/usr/bin/env python3
"""
Unit tests for the LangGraph → ADK Event translation layer.

The event shapes asserted here are the de-facto wire contract the dashboard
workroom, standalone chat and widget JS parse from /run_sse SSE frames.
"""

import asyncio
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from shared.utils.langgraph.event_translator import (
    ai_message_to_event,
    interrupt_to_event,
    text_of,
    tool_message_to_event,
    translate_stream,
)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


async def _collect(stream_items):
    """stream_items: (namespace, mode, payload) triples, as astream(subgraphs=True) yields."""
    async def fake_stream():
        for item in stream_items:
            yield item
    results = []
    async for event, complete in translate_stream(fake_stream(), author="my_agent",
                                                  invocation_id="e-inv1"):
        results.append((event, complete))
    return results


class TestTextOf(unittest.TestCase):

    def test_string_content(self):
        self.assertEqual(text_of("hello"), "hello")

    def test_block_list_content(self):
        blocks = [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {}}, "b"]
        self.assertEqual(text_of(blocks), "ab")

    def test_empty(self):
        self.assertEqual(text_of(None), "")
        self.assertEqual(text_of([]), "")


class TestAiMessageToEvent(unittest.TestCase):

    def test_text_message(self):
        msg = AIMessage(content="Zdravo!", usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        event = ai_message_to_event(msg, "my_agent", "e-inv1")
        self.assertEqual(event["author"], "my_agent")
        self.assertEqual(event["invocationId"], "e-inv1")
        self.assertEqual(event["content"]["role"], "model")
        self.assertEqual(event["content"]["parts"], [{"text": "Zdravo!"}])
        # both casings must be present — frontend tolerates either
        self.assertEqual(event["usageMetadata"]["prompt_token_count"], 10)
        self.assertEqual(event["usageMetadata"]["promptTokenCount"], 10)
        self.assertEqual(event["usageMetadata"]["candidates_token_count"], 5)
        self.assertEqual(event["usageMetadata"]["total_token_count"], 15)

    def test_tool_call_message(self):
        msg = AIMessage(content="", tool_calls=[
            {"name": "get_weather", "args": {"city": "Beograd"}, "id": "call_1"}])
        event = ai_message_to_event(msg, "my_agent", "e-inv1")
        fc = event["content"]["parts"][0]["functionCall"]
        self.assertEqual(fc["id"], "call_1")
        self.assertEqual(fc["name"], "get_weather")
        self.assertEqual(fc["args"], {"city": "Beograd"})
        self.assertNotIn("actions", event)

    def test_transfer_tool_call_sets_actions(self):
        msg = AIMessage(content="", tool_calls=[
            {"name": "transfer_to_agent", "args": {"agent_name": "child"}, "id": "call_2"}])
        event = ai_message_to_event(msg, "my_agent", "e-inv1")
        self.assertEqual(event["actions"]["transfer_to_agent"], "child")
        self.assertEqual(event["actions"]["transferToAgent"], "child")

    def test_empty_message_returns_none(self):
        self.assertIsNone(ai_message_to_event(AIMessage(content=""), "a", "i"))


class TestToolMessageToEvent(unittest.TestCase):

    def test_function_response_shape(self):
        msg = ToolMessage(content="sunny", name="get_weather", tool_call_id="call_1")
        event = tool_message_to_event(msg, "my_agent", "e-inv1")
        fr = event["content"]["parts"][0]["functionResponse"]
        self.assertEqual(fr["id"], "call_1")
        self.assertEqual(fr["name"], "get_weather")
        self.assertEqual(fr["response"], {"result": "sunny"})


class TestInterruptToEvent(unittest.TestCase):

    def test_confirmation_call_shape(self):
        value = {"originalFunctionCall": {"id": "call_9", "name": "delete_all", "args": {}},
                 "toolConfirmation": {"hint": "Confirm?"}}
        event = interrupt_to_event(value, "my_agent", "e-inv1")
        fc = event["content"]["parts"][0]["functionCall"]
        self.assertEqual(fc["name"], "adk_request_confirmation")
        self.assertEqual(fc["id"], "call_9")
        self.assertEqual(fc["args"]["originalFunctionCall"]["name"], "delete_all")


class TestTranslateStream(unittest.TestCase):

    def test_partial_frames_then_complete_event(self):
        chunk1 = AIMessageChunk(content="Beo")
        chunk2 = AIMessageChunk(content="grad")
        final = AIMessage(content="Beograd", usage_metadata={
            "input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
        items = [
            ((), "messages", (chunk1, {"langgraph_node": "agent"})),
            ((), "messages", (chunk2, {"langgraph_node": "agent"})),
            ((), "updates", {"agent": {"messages": [final]}}),
        ]
        results = _run(_collect(items))
        self.assertEqual(len(results), 3)

        partial1, complete1 = results[0]
        self.assertFalse(complete1)
        self.assertTrue(partial1["partial"])
        self.assertEqual(partial1["content"]["parts"], [{"text": "Beo"}])

        final_event, is_complete = results[2]
        self.assertTrue(is_complete)
        self.assertNotIn("partial", final_event)
        self.assertEqual(final_event["content"]["parts"], [{"text": "Beograd"}])
        self.assertIn("usageMetadata", final_event)

    def test_empty_chunks_are_skipped(self):
        items = [((), "messages", (AIMessageChunk(content=""), {}))]
        self.assertEqual(_run(_collect(items)), [])

    def test_tool_flow_events(self):
        call_msg = AIMessage(content="", tool_calls=[
            {"name": "lookup", "args": {}, "id": "c1"}])
        tool_msg = ToolMessage(content="42", name="lookup", tool_call_id="c1")
        items = [
            ((), "updates", {"agent": {"messages": [call_msg]}}),
            ((), "updates", {"tools": {"messages": [tool_msg]}}),
        ]
        results = _run(_collect(items))
        self.assertEqual(len(results), 2)
        self.assertIn("functionCall", results[0][0]["content"]["parts"][0])
        self.assertIn("functionResponse", results[1][0]["content"]["parts"][0])
        self.assertTrue(all(complete for _, complete in results))

    def test_interrupt_yields_confirmation_event(self):
        class FakeInterrupt:
            value = {"originalFunctionCall": {"id": "c2", "name": "danger", "args": {}}}
        items = [((), "updates", {"__interrupt__": [FakeInterrupt()]})]
        results = _run(_collect(items))
        self.assertEqual(len(results), 1)
        event, complete = results[0]
        self.assertTrue(complete)
        fc = event["content"]["parts"][0]["functionCall"]
        self.assertEqual(fc["name"], "adk_request_confirmation")

    def test_subgraph_namespace_sets_author(self):
        # messages/updates coming from a sub-agent's subgraph carry its name in the namespace
        final = AIMessage(content="from child")
        chunk = AIMessageChunk(content="fro")
        items = [
            (("child_agent:uuid-123",), "messages", (chunk, {"langgraph_node": "agent"})),
            (("child_agent:uuid-123",), "updates", {"agent": {"messages": [final]}}),
        ]
        results = _run(_collect(items))
        self.assertEqual(results[0][0]["author"], "child_agent")
        self.assertEqual(results[1][0]["author"], "child_agent")

    def test_parent_level_agent_node_updates_are_skipped(self):
        # parent-graph node updates (named after sub-agents) repeat messages already
        # emitted from the subgraph — they must not produce duplicate events
        final = AIMessage(content="hi")
        items = [((), "updates", {"child_agent": {"messages": [final]}})]
        self.assertEqual(_run(_collect(items)), [])


if __name__ == "__main__":
    unittest.main()
