#!/usr/bin/env python3
"""
The OpenAI <-> ADK translation the bridge depends on.

These cover the parts that were silently wrong before tool calling existed: the
session id that collapsed every conversation into one, the delta arithmetic that
duplicated text, and the fact that a caller's tools and tool results were
dropped on the floor.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.openai_translate import (MAX_IMAGE_BYTES, TextDeltaTracker,
                                     build_runtime_turns, consumed_index,
                                     conversation_key, extract_content_text,
                                     image_parts, iter_sse_payloads,
                                     normalize_client_tools, system_text,
                                     tool_call_chunk)

SYSTEM = {"role": "system", "content": "You are a coding agent."}


def _tool(name, properties=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} something",
            "parameters": {"type": "object", "properties": properties or {}},
        },
    }


class TestClientToolNormalisation(unittest.TestCase):

    def test_tools_become_flat_declarations(self):
        declarations = normalize_client_tools([_tool("read"), _tool("bash")])
        self.assertEqual([d["name"] for d in declarations], ["read", "bash"])
        self.assertEqual(declarations[0]["parameters"]["type"], "object")

    def test_no_tools_is_not_an_error(self):
        self.assertEqual(normalize_client_tools(None), [])
        self.assertEqual(normalize_client_tools([]), [])

    def test_tool_choice_none_withdraws_every_tool(self):
        self.assertEqual(normalize_client_tools([_tool("read")], "none"), [])

    def test_a_named_tool_choice_narrows_to_that_tool(self):
        choice = {"type": "function", "function": {"name": "bash"}}
        declarations = normalize_client_tools([_tool("read"), _tool("bash")], choice)
        self.assertEqual([d["name"] for d in declarations], ["bash"])

    def test_auto_passes_everything_through(self):
        declarations = normalize_client_tools([_tool("read"), _tool("bash")], "auto")
        self.assertEqual(len(declarations), 2)


class TestConversationIdentity(unittest.TestCase):
    """
    The bug this guards: keying the session on the FIRST message merges every
    conversation a coding agent opens, because messages[0] is a constant system
    prompt shared by all of them.
    """

    def test_a_shared_system_prompt_does_not_merge_conversations(self):
        first = conversation_key("agent", "u1", [SYSTEM, {"role": "user", "content": "fix the parser"}])
        second = conversation_key("agent", "u1", [SYSTEM, {"role": "user", "content": "add a test"}])
        self.assertNotEqual(first, second)

    def test_a_rewritten_system_prompt_keeps_the_conversation(self):
        """
        Cline and Roo swap the system prompt when you toggle Plan and Act, and
        clients change it on upgrade. Hashing it dropped the agent's memory of
        the conversation in the middle of a task.
        """
        plan = [{"role": "system", "content": "PLAN MODE. Do not edit files."},
                {"role": "user", "content": "fix the parser"}]
        act = [{"role": "system", "content": "ACT MODE. Edit files as needed."},
               {"role": "user", "content": "fix the parser"}]
        self.assertEqual(conversation_key("agent", "u1", plan),
                         conversation_key("agent", "u1", act))

    def test_a_client_supplied_id_decides_on_its_own(self):
        a = [SYSTEM, {"role": "user", "content": "fix the parser"}]
        b = [{"role": "user", "content": "something else entirely"}]
        self.assertEqual(conversation_key("agent", "u1", a, "thread-7"),
                         conversation_key("agent", "u1", b, "thread-7"))
        self.assertNotEqual(conversation_key("agent", "u1", a, "thread-7"),
                            conversation_key("agent", "u1", a, "thread-8"))

    def test_the_id_is_stable_across_a_conversation(self):
        start = [SYSTEM, {"role": "user", "content": "fix the parser"}]
        later = start + [{"role": "assistant", "content": "done"},
                         {"role": "user", "content": "now what"}]
        self.assertEqual(conversation_key("agent", "u1", start),
                         conversation_key("agent", "u1", later))

    def test_different_agents_and_users_do_not_share_a_session(self):
        messages = [SYSTEM, {"role": "user", "content": "hi"}]
        self.assertNotEqual(conversation_key("a", "u1", messages),
                            conversation_key("b", "u1", messages))
        self.assertNotEqual(conversation_key("a", "u1", messages),
                            conversation_key("a", "u2", messages))

    def test_the_origin_prefix_survives(self):
        # response_metrics classifies an invocation's origin from this prefix.
        key = conversation_key("agent", "u1", [{"role": "user", "content": "hi"}])
        self.assertTrue(key.startswith("openai_sess_"))


def _screenshot(url="data:image/png;base64,aGVsbG8="):
    return {"type": "image_url", "image_url": {"url": url}}


class TestScreenshots(unittest.TestCase):
    """
    A client that attaches a screenshot must not have it silently dropped — and
    when it cannot be forwarded, the model has to be told, or it answers about
    an image it never received.
    """

    def test_an_attached_image_becomes_inline_data(self):
        parts = image_parts([{"type": "text", "text": "what is this"}, _screenshot()])
        self.assertEqual(parts, [{"inline_data": {"mime_type": "image/png",
                                                  "data": "aGVsbG8="}}])

    def test_the_image_rides_with_the_user_turn(self):
        messages = [{"role": "user",
                     "content": [{"type": "text", "text": "why does this fail"},
                                 _screenshot()]}]
        turns = build_runtime_turns(messages, 0)
        self.assertEqual(len(turns), 1)
        parts = turns[0]["parts"]
        self.assertEqual(parts[0], {"text": "why does this fail"})
        self.assertIn("inline_data", parts[1])

    def test_an_image_only_message_still_makes_a_turn(self):
        turns = build_runtime_turns([{"role": "user", "content": [_screenshot()]}], 0)
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(turns[0]["parts"]), 1)
        self.assertIn("inline_data", turns[0]["parts"][0])

    def test_a_remote_url_is_refused_rather_than_fetched(self):
        # Fetching a caller-supplied URL server-side would be an SSRF.
        parts = image_parts([_screenshot("https://example.com/shot.png")])
        self.assertEqual(len(parts), 1)
        self.assertIn("not a URL", parts[0]["text"])
        self.assertNotIn("inline_data", parts[0])

    def test_a_text_only_model_is_told_the_image_was_dropped(self):
        parts = image_parts([_screenshot()], vision=False)
        self.assertIn("no vision support", parts[0]["text"])

    def test_an_oversized_image_is_described_instead_of_sent(self):
        huge = "A" * (MAX_IMAGE_BYTES * 2)
        parts = image_parts([_screenshot(f"data:image/png;base64,{huge}")])
        self.assertIn("larger than", parts[0]["text"])

    def test_a_plain_string_content_has_no_images(self):
        self.assertEqual(image_parts("just text"), [])

    def test_the_system_preamble_still_leads_the_turn(self):
        messages = [SYSTEM, {"role": "user",
                             "content": [{"type": "text", "text": "look"}, _screenshot()]}]
        turns = build_runtime_turns(messages, 0, system_text(messages))
        self.assertIn("You are a coding agent.", turns[0]["parts"][0]["text"])
        self.assertIn("inline_data", turns[0]["parts"][1])


class TestWhatTheTurnStillOwes(unittest.TestCase):

    def test_a_fresh_conversation_owes_everything(self):
        messages = [SYSTEM, {"role": "user", "content": "hi"}]
        self.assertEqual(consumed_index(messages), 0)

    def test_history_is_consumed_up_to_the_last_assistant_message(self):
        messages = [SYSTEM,
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "again"}]
        self.assertEqual(consumed_index(messages), 3)
        turns = build_runtime_turns(messages, consumed_index(messages))
        self.assertEqual(turns, [{"role": "user", "parts": [{"text": "again"}]}])

    def test_the_system_prompt_rides_with_the_opening_message(self):
        messages = [SYSTEM, {"role": "user", "content": "hi"}]
        turns = build_runtime_turns(messages, 0, system_text(messages))
        self.assertEqual(len(turns), 1)
        self.assertIn("You are a coding agent.", turns[0]["parts"][0]["text"])
        self.assertIn("hi", turns[0]["parts"][0]["text"])

    def test_tool_results_become_function_responses(self):
        messages = [SYSTEM,
                    {"role": "user", "content": "read it"},
                    {"role": "assistant", "content": None},
                    {"role": "tool", "tool_call_id": "call_1", "name": "read",
                     "content": "file body"}]
        turns = build_runtime_turns(messages, consumed_index(messages))
        self.assertEqual(turns, [{
            "role": "user",
            "parts": [{"function_response": {"id": "call_1", "name": "read",
                                             "response": {"result": "file body"}}}],
        }])

    def test_parallel_tool_results_travel_together(self):
        messages = [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": None},
                    {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "a"},
                    {"role": "tool", "tool_call_id": "c2", "name": "grep", "content": "b"}]
        turns = build_runtime_turns(messages, consumed_index(messages))
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(turns[0]["parts"]), 2)

    def test_tool_results_and_new_text_are_split_into_two_turns(self):
        # ADK's Runner refuses a message carrying both, so they cannot be merged.
        messages = [{"role": "user", "content": "go"},
                    {"role": "assistant", "content": None},
                    {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "a"},
                    {"role": "user", "content": "actually, stop"}]
        turns = build_runtime_turns(messages, consumed_index(messages))
        self.assertEqual(len(turns), 2)
        self.assertIn("function_response", turns[0]["parts"][0])
        self.assertEqual(turns[1]["parts"][0]["text"], "actually, stop")

    def test_nothing_new_owes_nothing(self):
        messages = [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"}]
        self.assertEqual(build_runtime_turns(messages, consumed_index(messages)), [])


class TestTextDeltas(unittest.TestCase):
    """
    ADK streams partial frames and then repeats the whole segment. Emitting
    frames as-is sends the answer twice.
    """

    def test_a_cumulative_stream_yields_only_the_new_text(self):
        tracker = TextDeltaTracker()
        self.assertEqual(tracker.feed("a", "Hel"), "Hel")
        self.assertEqual(tracker.feed("a", "Hello wor"), "lo wor")
        self.assertEqual(tracker.feed("a", "Hello world"), "ld")

    def test_a_repeat_of_what_was_sent_is_dropped(self):
        tracker = TextDeltaTracker()
        tracker.feed("a", "Hello world")
        self.assertEqual(tracker.feed("a", "Hello"), "")

    def test_a_new_segment_is_not_glued_to_the_last_one(self):
        tracker = TextDeltaTracker()
        tracker.feed("a", "First answer")
        self.assertEqual(tracker.feed("a", "Second"), "Second")
        self.assertEqual(tracker.feed("a", "Second answer"), " answer")

    def test_another_agent_taking_over_resets_the_segment(self):
        tracker = TextDeltaTracker()
        tracker.feed("root", "Delegating")
        self.assertEqual(tracker.feed("child", "Working"), "Working")

    def test_a_tool_call_ends_the_segment(self):
        tracker = TextDeltaTracker()
        tracker.feed("a", "Let me look")
        tracker.reset_segment()
        self.assertEqual(tracker.feed("a", "Found it"), "Found it")


class TestChunkShapes(unittest.TestCase):

    def test_a_tool_call_chunk_carries_what_the_ai_sdk_accumulates(self):
        chunk = tool_call_chunk("id", "agent", 0, 0, "call_1", "read", {"path": "a.py"})
        call = chunk["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual(call["index"], 0)
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "read")
        # arguments are a JSON string, not an object
        self.assertEqual(call["function"]["arguments"], '{"path": "a.py"}')


class TestSseFraming(unittest.TestCase):

    def test_a_partial_frame_is_held_until_it_completes(self):
        payloads, rest = iter_sse_payloads('data: {"a":1}\n\ndata: {"b"')
        self.assertEqual(payloads, ['{"a":1}'])
        self.assertEqual(rest, 'data: {"b"')

    def test_non_data_lines_are_ignored(self):
        payloads, _ = iter_sse_payloads("event: message\ndata: {}\n")
        self.assertEqual(payloads, ["{}"])


class TestContentExtraction(unittest.TestCase):

    def test_a_plain_string(self):
        self.assertEqual(extract_content_text("hello"), "hello")

    def test_a_part_list(self):
        self.assertEqual(
            extract_content_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]),
            "ab")

    def test_a_null_content_is_empty_not_none(self):
        self.assertEqual(extract_content_text(None), "")


if __name__ == "__main__":
    unittest.main()
