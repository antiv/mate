#!/usr/bin/env python3
"""
Reading a run's reply out of /run_sse (agent_invoke.ReplyCollector), which the
trigger runner, the approvals resume and the Slack bridge share.
"""

import json
import unittest

from shared.utils.agent_invoke import ReplyCollector


def frame(author, *parts):
    return f"data: {json.dumps({'author': author, 'content': {'parts': list(parts)}})}\n\n".encode()


def read(*chunks):
    collector = ReplyCollector()
    for chunk in chunks:
        collector.feed(chunk)
    return collector.reply


class TestReplyCollector(unittest.TestCase):

    def test_streamed_deltas_are_merged_not_doubled(self):
        self.assertEqual(read(frame("a", {"text": "Hel"}), frame("a", {"text": "Hello"}),
                              frame("a", {"text": " there"}), frame("a", {"text": "Hello there"})),
                         "Hello there")

    def test_text_before_a_tool_call_is_dropped(self):
        self.assertEqual(read(frame("a", {"text": "Let me look."}),
                              frame("a", {"functionCall": {"name": "search"}}),
                              frame("a", {"text": "Found it."})),
                         "Found it.")

    def test_the_last_author_answers(self):
        self.assertEqual(read(frame("root", {"text": "Asking the expert."}),
                              frame("expert", {"text": "42"})), "42")

    def test_a_frame_split_across_chunks_and_noise_between(self):
        whole = frame("a", {"text": "split reply"})
        self.assertEqual(read(b": keep-alive\n", whole[:10], whole[10:], b"data: not json\n"),
                         "split reply")

    def test_carriage_returns_and_done(self):
        self.assertEqual(read(frame("a", {"text": "ok"}).replace(b"\n", b"\r\n", 1), b"data: [DONE]\n"), "ok")


if __name__ == "__main__":
    unittest.main()
