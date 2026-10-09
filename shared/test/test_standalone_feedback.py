#!/usr/bin/env python3
"""
Unit tests for forwarding a standalone build's ratings to a central MATE.

The standalone chat has no login, so the route must be throttled, must refuse
anything but a well-formed rating, and must take the rated exchange from the
build's own session rather than from the browser.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import standalone_feedback as sf


class TestFeedbackTarget(unittest.TestCase):

    def test_needs_both_url_and_key(self):
        cases = [
            ({}, None),
            ({"MATE_FEEDBACK_URL": "https://mate.example.com"}, None),
            ({"MATE_FEEDBACK_KEY": "wk_1"}, None),
            ({"MATE_FEEDBACK_URL": " https://mate.example.com/ ", "MATE_FEEDBACK_KEY": "wk_1"},
             ("https://mate.example.com", "wk_1")),
        ]
        for env, expected in cases:
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(sf.feedback_target(), expected, env)


class TestRateLimiter(unittest.TestCase):

    def test_refuses_past_the_limit_per_key(self):
        limiter = sf.RateLimiter(limit=2, window=60)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))

    def test_allows_again_once_the_window_passes(self):
        limiter = sf.RateLimiter(limit=1, window=60)
        with patch("shared.utils.standalone_feedback.time.monotonic", side_effect=[0, 30, 61]):
            self.assertTrue(limiter.allow("a"))
            self.assertFalse(limiter.allow("a"))
            self.assertTrue(limiter.allow("a"))


class TestParseRating(unittest.TestCase):

    def _body(self, **overrides):
        body = {"session_id": "s1", "message_id": "inv", "user_id": "u1", "rating": "down"}
        body.update(overrides)
        return body

    def test_accepts_a_rating(self):
        parsed = sf.parse_rating(self._body(comment="wrong"))
        self.assertEqual(parsed["rating"], "down")
        self.assertEqual(parsed["comment"], "wrong")

    def test_rejects_missing_or_bad_fields(self):
        for body in ([], self._body(rating="maybe"), self._body(user_id=""),
                     self._body(session_id=None), self._body(message_id=7)):
            with self.assertRaises(ValueError, msg=body):
                sf.parse_rating(body)

    def test_a_question_or_answer_from_the_browser_is_dropped(self):
        parsed = sf.parse_rating(self._body(question="made up", answer="made up"))
        self.assertNotIn("question", parsed)
        self.assertNotIn("answer", parsed)

    def test_comment_is_capped(self):
        parsed = sf.parse_rating(self._body(comment="x" * (sf.MAX_COMMENT + 5)))
        self.assertEqual(len(parsed["comment"]), sf.MAX_COMMENT)


class TestReadExchange(unittest.TestCase):

    def test_reads_the_rated_exchange_from_the_session(self):
        from google.adk.events import Event
        from google.adk.sessions.in_memory_session_service import InMemorySessionService
        from google.genai import types

        async def run():
            service = InMemorySessionService()
            session = await service.create_session(app_name="agent", user_id="u1")
            for author, role, text in (("user", "user", "2+2?"), ("agent", "model", "5")):
                await service.append_event(session, Event(
                    invocation_id="inv", author=author,
                    content=types.Content(role=role, parts=[types.Part(text=text)])))
            found = await sf.read_exchange(service, "agent", "u1", session.id, "inv")
            # Another user's id must not open the session
            other = await sf.read_exchange(service, "agent", "u2", session.id, "inv")
            return found, other

        found, other = asyncio.run(run())
        self.assertEqual(found, ("2+2?", "5"))
        self.assertIsNone(other)


class TestForward(unittest.TestCase):

    def _forward_with(self, handler):
        transport = httpx.MockTransport(handler)
        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=transport, **kwargs)

        with patch("shared.utils.standalone_feedback.httpx.AsyncClient", side_effect=client):
            return asyncio.run(sf.forward("https://mate.example.com", "wk_1", {"rating": "up"}))

    def test_posts_to_the_widget_feedback_route_with_the_key(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["key"] = request.headers.get("X-Widget-Key")
            return httpx.Response(200, json={})

        self.assertEqual(self._forward_with(handler), (True, "HTTP 200"))
        self.assertEqual(seen, {"url": "https://mate.example.com/widget/api/feedback",
                                "key": "wk_1"})

    def test_a_refusal_is_reported_not_raised(self):
        self.assertEqual(self._forward_with(lambda r: httpx.Response(401)), (False, "HTTP 401"))

    def test_a_network_error_is_reported_not_raised(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        ok, detail = self._forward_with(handler)
        self.assertFalse(ok)
        self.assertEqual(detail, "ConnectError")


if __name__ == '__main__':
    unittest.main()
