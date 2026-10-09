#!/usr/bin/env python3
"""
Unit tests for response feedback.

The satisfaction rate must count responses, not clicks: a visitor who changes their
mind has to overwrite their rating rather than add a second one. The public endpoint
is reachable with nothing but a widget key, so the agent and project it records must
come from the key, never from the request body.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.feedback_service import FeedbackService, MAX_COMMENT
from shared.utils.models import Base, ResponseFeedback


class TestFeedbackService(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db_client = MagicMock()
        self.db_client.is_connected.return_value = True
        self.db_client.get_session.side_effect = lambda: self.Session()
        with patch("shared.utils.feedback_service.get_database_client",
                   return_value=self.db_client):
            self.service = FeedbackService()

    def tearDown(self):
        self.engine.dispose()

    def _count(self):
        session = self.Session()
        try:
            return session.query(ResponseFeedback).count()
        finally:
            session.close()

    def test_records_a_rating(self):
        result = self.service.submit(session_id="s1", message_id="e-1", rating="up",
                                     agent_name="a1", project_id=7)
        self.assertEqual(result["rating"], "up")
        self.assertEqual(result["agent_name"], "a1")
        self.assertEqual(result["project_id"], 7)
        self.assertEqual(self._count(), 1)

    def test_changing_a_rating_overwrites_it(self):
        # Otherwise one indecisive visitor moves the satisfaction rate on their own
        self.service.submit(session_id="s1", message_id="e-1", rating="up")
        self.service.submit(session_id="s1", message_id="e-1", rating="down")
        self.assertEqual(self._count(), 1)
        session = self.Session()
        self.assertEqual(session.query(ResponseFeedback).one().rating, "down")
        session.close()

    def test_different_messages_are_separate_ratings(self):
        self.service.submit(session_id="s1", message_id="e-1", rating="up")
        self.service.submit(session_id="s1", message_id="e-2", rating="up")
        self.assertEqual(self._count(), 2)

    def test_same_message_id_in_another_session_is_separate(self):
        self.service.submit(session_id="s1", message_id="e-1", rating="up")
        self.service.submit(session_id="s2", message_id="e-1", rating="up")
        self.assertEqual(self._count(), 2)

    def test_rejects_an_unknown_rating(self):
        self.assertIsNone(self.service.submit(session_id="s1", message_id="e-1",
                                              rating="sideways"))
        self.assertEqual(self._count(), 0)

    def test_rejects_missing_identifiers(self):
        self.assertIsNone(self.service.submit(session_id="", message_id="e-1", rating="up"))
        self.assertIsNone(self.service.submit(session_id="s1", message_id="", rating="up"))

    def test_comment_is_truncated(self):
        result = self.service.submit(session_id="s1", message_id="e-1", rating="down",
                                     comment="x" * (MAX_COMMENT + 500))
        self.assertEqual(len(result["comment"]), MAX_COMMENT)

    def test_changing_a_rating_keeps_an_earlier_comment(self):
        self.service.submit(session_id="s1", message_id="e-1", rating="down",
                            comment="was wrong about the price")
        result = self.service.submit(session_id="s1", message_id="e-1", rating="up")
        self.assertEqual(result["comment"], "was wrong about the price")

    def test_stores_the_exchange_a_standalone_build_sends(self):
        self.service.submit(session_id="s1", message_id="e-1", rating="down",
                            question="2+2?", answer="5")
        session = self.Session()
        row = session.query(ResponseFeedback).one()
        self.assertEqual((row.question, row.answer), ("2+2?", "5"))
        session.close()

    def test_a_later_rating_without_the_exchange_keeps_it(self):
        # The note after a thumbs-down is a second submit; it must not blank the exchange
        self.service.submit(session_id="s1", message_id="e-1", rating="down",
                            question="2+2?", answer="5")
        self.service.submit(session_id="s1", message_id="e-1", rating="down", comment="wrong")
        session = self.Session()
        row = session.query(ResponseFeedback).one()
        self.assertEqual((row.question, row.answer, row.comment), ("2+2?", "5", "wrong"))
        session.close()

    def test_the_exchange_is_truncated(self):
        from shared.utils.feedback_service import MAX_EXCHANGE
        self.service.submit(session_id="s1", message_id="e-1", rating="down",
                            question="q" * (MAX_EXCHANGE + 10), answer="a")
        session = self.Session()
        self.assertEqual(len(session.query(ResponseFeedback).one().question), MAX_EXCHANGE)
        session.close()

    def test_another_agents_key_cannot_take_over_a_rating(self):
        self.service.submit(session_id="s1", message_id="e-1", rating="down",
                            agent_name="a1", question="2+2?", answer="5")
        self.assertIsNone(self.service.submit(session_id="s1", message_id="e-1", rating="up",
                                              agent_name="other", question="made up"))
        session = self.Session()
        row = session.query(ResponseFeedback).one()
        self.assertEqual((row.rating, row.question, row.agent_name), ("down", "2+2?", "a1"))
        session.close()

    def test_ratings_for_a_session_are_readable(self):
        self.service.submit(session_id="s1", message_id="e-1", rating="up")
        self.service.submit(session_id="s1", message_id="e-2", rating="down")
        self.service.submit(session_id="s2", message_id="e-3", rating="up")
        self.assertEqual(self.service.get_for_session("s1"), {"e-1": "up", "e-2": "down"})

    def test_no_database_degrades_quietly(self):
        self.db_client.is_connected.return_value = False
        self.assertIsNone(self.service.submit(session_id="s1", message_id="e-1", rating="up"))
        self.assertEqual(self.service.get_for_session("s1"), {})


class TestWidgetFeedbackEndpoint(unittest.TestCase):
    """The public surface: a widget key is the only credential a visitor has."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import server.widget_routes as wr

        self.wr = wr
        self.widget_key = MagicMock(id=1, agent_name="support_root", project_id=42)

        app = FastAPI()
        app.include_router(wr.router)
        self.from_build = False
        app.dependency_overrides[wr.verify_feedback_sender] = lambda: (self.widget_key, self.from_build)
        self.client = TestClient(app)

        self.service = MagicMock()
        self.service.submit.return_value = {"message_id": "e-1", "rating": "up"}
        self.patcher = patch("shared.utils.feedback_service.get_feedback_service",
                             return_value=self.service)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_records_a_rating(self):
        resp = self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "up"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["feedback"]["rating"], "up")

    def test_agent_and_project_come_from_the_key(self):
        # A visitor must not be able to attribute a rating to someone else's agent
        self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "up",
            "agent_name": "someone_elses_agent", "project_id": 999})
        kwargs = self.service.submit.call_args.kwargs
        self.assertEqual(kwargs["agent_name"], "support_root")
        self.assertEqual(kwargs["project_id"], 42)

    def test_passes_on_the_exchange_a_standalone_build_sends(self):
        self.from_build = True
        self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "down",
            "question": "2+2?", "answer": "5"})
        kwargs = self.service.submit.call_args.kwargs
        self.assertEqual((kwargs["question"], kwargs["answer"]), ("2+2?", "5"))

    def test_ignores_an_exchange_that_is_not_text(self):
        self.from_build = True
        self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "down",
            "question": {"x": 1}, "answer": ["5"]})
        kwargs = self.service.submit.call_args.kwargs
        self.assertIsNone(kwargs["question"])
        self.assertIsNone(kwargs["answer"])

    def test_the_public_key_cannot_send_an_exchange(self):
        # It is in every page that embeds the widget; the text reaches Suggest a fix
        self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "down",
            "question": "made up", "answer": "made up"})
        kwargs = self.service.submit.call_args.kwargs
        self.assertIsNone(kwargs["question"])
        self.assertIsNone(kwargs["answer"])

    def test_rejects_a_bad_rating(self):
        resp = self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "maybe"})
        self.assertEqual(resp.status_code, 400)

    def test_rejects_missing_identifiers(self):
        resp = self.client.post("/widget/api/feedback", json={"rating": "up"})
        self.assertEqual(resp.status_code, 400)

    def test_storage_failure_is_reported(self):
        self.service.submit.return_value = None
        resp = self.client.post("/widget/api/feedback", json={
            "session_id": "s1", "message_id": "e-1", "rating": "up"})
        self.assertEqual(resp.status_code, 500)


class TestFeedbackSender(unittest.TestCase):
    """Which credential the feedback route was called with."""

    def _request(self, headers):
        from starlette.requests import Request
        return Request({"type": "http", "method": "POST", "path": "/widget/api/feedback",
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                        "query_string": b""})

    def test_the_feedback_key_marks_a_standalone_build(self):
        import server.widget_routes as wr
        wk = MagicMock(agent_name="a1")
        with patch.object(wr, "_lookup_widget_feedback_key", return_value=wk) as lookup:
            self.assertEqual(wr.verify_feedback_sender(self._request({"X-Widget-Feedback-Key": "wfk_1"})),
                             (wk, True))
        lookup.assert_called_once_with("wfk_1")

    def test_an_unknown_feedback_key_is_refused(self):
        import server.widget_routes as wr
        from fastapi import HTTPException
        with patch.object(wr, "_lookup_widget_feedback_key", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                wr.verify_feedback_sender(self._request({"X-Widget-Feedback-Key": "wfk_bad"}))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_the_public_key_is_not_a_standalone_build(self):
        import server.widget_routes as wr
        wk = MagicMock(agent_name="a1")
        with patch.object(wr, "verify_widget_key", return_value=wk):
            self.assertEqual(wr.verify_feedback_sender(self._request({"X-Widget-Key": "wk_1"})),
                             (wk, False))


class TestFeedbackIsRateLimited(unittest.TestCase):

    def test_middleware_covers_the_public_feedback_path(self):
        # It is reachable with only a public key, so it must not be unthrottled
        import inspect
        from server import rate_limit_middleware
        source = inspect.getsource(rate_limit_middleware)
        self.assertIn("/widget/api/feedback", source)


if __name__ == '__main__':
    unittest.main()
