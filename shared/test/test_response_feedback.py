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
        app.dependency_overrides[wr.verify_widget_key] = lambda: self.widget_key
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


class TestFeedbackIsRateLimited(unittest.TestCase):

    def test_middleware_covers_the_public_feedback_path(self):
        # It is reachable with only a public key, so it must not be unthrottled
        import inspect
        from server import rate_limit_middleware
        source = inspect.getsource(rate_limit_middleware)
        self.assertIn("/widget/api/feedback", source)


if __name__ == '__main__':
    unittest.main()
