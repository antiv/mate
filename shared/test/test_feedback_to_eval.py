#!/usr/bin/env python3
"""
Unit tests for turning a thumbs-down response into an eval test case.

A rating stores only the session and the invocation id, so the question and the
answer have to be recovered from the session history, which the two runtimes
serialise differently: ADK's store writes `invocation_id`, the LangGraph store
`invocationId`. The list must survive a session that has since been deleted, and
the same response must not become two test cases.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.feedback_service import FeedbackService, extract_exchange
from shared.utils.models import (Base, LangGraphEvent, LangGraphSession,
                                 ResponseFeedback, TestCase)

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _text(role, text, **extra):
    return {"role": role, "parts": [{"text": text, **extra}]}


class TestExtractExchange(unittest.TestCase):

    def test_adk_shape(self):
        events = [
            {"invocation_id": "inv-1", "author": "user", "content": _text("user", "What is 2+2?")},
            {"invocation_id": "inv-1", "author": "root", "content": _text("model", "5")},
        ]
        self.assertEqual(extract_exchange(events, "inv-1"), ("What is 2+2?", "5"))

    def test_langgraph_shape(self):
        events = [
            {"invocationId": "inv-1", "author": "user", "content": _text("user", "Hi")},
            {"invocationId": "inv-1", "author": "root", "content": _text("model", "Hello")},
        ]
        self.assertEqual(extract_exchange(events, "inv-1"), ("Hi", "Hello"))

    def test_other_invocations_are_ignored(self):
        events = [
            {"invocation_id": "inv-1", "author": "user", "content": _text("user", "first")},
            {"invocation_id": "inv-1", "author": "root", "content": _text("model", "one")},
            {"invocation_id": "inv-2", "author": "user", "content": _text("user", "second")},
            {"invocation_id": "inv-2", "author": "root", "content": _text("model", "two")},
        ]
        self.assertEqual(extract_exchange(events, "inv-2"), ("second", "two"))

    def test_partial_events_and_thoughts_are_not_the_answer(self):
        # The final event repeats what the partials streamed
        events = [
            {"invocation_id": "i", "author": "user", "content": _text("user", "q")},
            {"invocation_id": "i", "author": "root", "partial": True, "content": _text("model", "The ans")},
            {"invocation_id": "i", "author": "root", "content": _text("model", "thinking...", thought=True)},
            {"invocation_id": "i", "author": "root", "content": _text("model", "The answer")},
        ]
        self.assertEqual(extract_exchange(events, "i"), ("q", "The answer"))

    def test_answer_spanning_a_tool_call_is_joined(self):
        events = [
            {"invocation_id": "i", "author": "user", "content": _text("user", "q")},
            {"invocation_id": "i", "author": "root", "content": _text("model", "Let me look.")},
            {"invocation_id": "i", "author": "root",
             "content": {"role": "model", "parts": [{"function_call": {"name": "search"}}]}},
            {"invocation_id": "i", "author": "sub", "content": _text("model", "Found it.")},
        ]
        self.assertEqual(extract_exchange(events, "i"), ("q", "Let me look.\n\nFound it."))

    def test_missing_invocation_gives_none(self):
        self.assertEqual(extract_exchange([], "nope"), (None, None))
        self.assertEqual(extract_exchange([{"invocation_id": "x", "author": "root"}], "x"),
                         (None, None))


class _DbTestCase(unittest.TestCase):

    def setUp(self):
        # StaticPool: one in-memory database shared by every session, including
        # the ones the endpoint opens from a worker thread
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
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

    def _add(self, *rows):
        session = self.Session()
        try:
            session.add_all(rows)
            session.commit()
            return [r.id for r in rows]
        finally:
            session.close()


class TestListRatedDown(_DbTestCase):

    def test_lists_only_thumbs_down(self):
        self._add(ResponseFeedback(session_id="s", message_id="m1", rating="up", agent_name="a"),
                  ResponseFeedback(session_id="s", message_id="m2", rating="down", agent_name="a"))
        rows = self.service.list_rated_down()
        self.assertEqual([r["message_id"] for r in rows], ["m2"])
        self.assertIsNone(rows[0]["test_case_id"])

    def test_filters_by_agent(self):
        self._add(ResponseFeedback(session_id="s", message_id="m1", rating="down", agent_name="a"),
                  ResponseFeedback(session_id="s", message_id="m2", rating="down", agent_name="b"))
        self.assertEqual([r["message_id"] for r in self.service.list_rated_down(agent_name="b")],
                         ["m2"])
        self.assertEqual(self.service.rated_down_agents(), ["a", "b"])

    def test_links_the_active_test_case_made_from_it(self):
        fb_id, = self._add(ResponseFeedback(session_id="s", message_id="m", rating="down",
                                            agent_name="a"))
        tc_id, = self._add(TestCase(agent_name="a", input="q", expected_output="e",
                                    source_feedback_id=fb_id))
        self.assertEqual(self.service.list_rated_down()[0]["test_case_id"], tc_id)

    def test_a_deactivated_test_case_frees_the_response(self):
        fb_id, = self._add(ResponseFeedback(session_id="s", message_id="m", rating="down",
                                            agent_name="a"))
        self._add(TestCase(agent_name="a", input="q", expected_output="e",
                           source_feedback_id=fb_id, is_active=False))
        self.assertIsNone(self.service.list_rated_down()[0]["test_case_id"])


class TestLangGraphEventsById(_DbTestCase):

    def test_events_are_found_without_app_or_user(self):
        from shared.utils.langgraph.session_store import SessionStore
        self._add(LangGraphSession(id="lg-1", app_name="app", user_id="u"))
        self._add(LangGraphEvent(id="e1", session_id="lg-1", author="user", invocation_id="inv",
                                 content=json.dumps(_text("user", "Hi")), timestamp=1.0),
                  LangGraphEvent(id="e2", session_id="lg-1", author="root", invocation_id="inv",
                                 content=json.dumps(_text("model", "Hello")), timestamp=2.0))
        with patch("shared.utils.langgraph.session_store.get_database_client",
                   return_value=self.db_client):
            events = SessionStore().get_events("lg-1")
        self.assertEqual(extract_exchange(events, "inv"), ("Hi", "Hello"))


class TestDashboardEndpoints(_DbTestCase):

    def setUp(self):
        super().setUp()
        from shared.utils.dashboard.dashboard_server import DashboardServer
        self.app = FastAPI()
        with patch("shared.utils.database_client.get_database_client",
                   return_value=self.db_client):
            self.server = DashboardServer(self.app, project_root=PROJECT_ROOT)
        self.server.db_client = self.db_client
        self.server.TestCase = TestCase
        self.app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "admin"
        self.is_admin = True
        self.server._get_is_admin = lambda request: self.is_admin
        self.sessions = {}
        self.server._get_session_events = lambda sid: self.sessions.get(sid)
        patcher = patch("shared.utils.feedback_service.get_feedback_service",
                        return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(self.app)

    def test_lists_question_and_answer_from_the_session(self):
        self._add(ResponseFeedback(session_id="s1", message_id="inv", rating="down",
                                   agent_name="a", comment="wrong"))
        self.sessions["s1"] = [
            {"invocation_id": "inv", "author": "user", "content": _text("user", "2+2?")},
            {"invocation_id": "inv", "author": "a", "content": _text("model", "5")},
        ]
        data = self.client.get("/dashboard/api/evals/feedback").json()
        row = data["responses"][0]
        self.assertEqual((row["question"], row["answer"], row["comment"]), ("2+2?", "5", "wrong"))
        self.assertTrue(row["session_available"])
        self.assertEqual(data["agents"], ["a"])

    def test_a_deleted_session_still_lists_the_rating(self):
        self._add(ResponseFeedback(session_id="gone", message_id="inv", rating="down",
                                   agent_name="a"))
        resp = self.client.get("/dashboard/api/evals/feedback")
        self.assertEqual(resp.status_code, 200)
        row = resp.json()["responses"][0]
        self.assertIsNone(row["question"])
        self.assertFalse(row["session_available"])

    def test_non_admin_cannot_read_visitor_conversations(self):
        self.is_admin = False
        self.assertEqual(self.client.get("/dashboard/api/evals/feedback").status_code, 403)

    def _create(self, **extra):
        body = {"agent_name": "a", "input": "2+2?", "expected_output": "4",
                "eval_method": "llm_judge", **extra}
        return self.client.post("/dashboard/api/evals", json=body)

    def test_creates_a_test_case_linked_to_the_rating(self):
        fb_id, = self._add(ResponseFeedback(session_id="s", message_id="m", rating="down",
                                            agent_name="a"))
        resp = self._create(source_feedback_id=fb_id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["test_case"]["source_feedback_id"], fb_id)
        self.assertEqual(self.service.list_rated_down()[0]["test_case_id"],
                         resp.json()["test_case"]["id"])

    def test_the_same_rating_cannot_become_two_test_cases(self):
        fb_id, = self._add(ResponseFeedback(session_id="s", message_id="m", rating="down",
                                            agent_name="a"))
        self.assertEqual(self._create(source_feedback_id=fb_id).status_code, 200)
        self.assertEqual(self._create(source_feedback_id=fb_id).status_code, 409)

    def test_rejects_an_unknown_or_malformed_source(self):
        self.assertEqual(self._create(source_feedback_id=999).status_code, 400)
        self.assertEqual(self._create(source_feedback_id="1").status_code, 400)
        self.assertEqual(self._create(source_feedback_id=True).status_code, 400)

    def test_a_test_case_without_a_source_is_unchanged(self):
        resp = self._create()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["test_case"]["source_feedback_id"])


class TestAdkSessionEvents(unittest.TestCase):
    """The ADK store is read with raw SQL, so check it against ADK's real serialisation."""

    def test_reads_events_as_adk_writes_them(self):
        from google.adk.events import Event
        from google.genai import types
        from shared.utils.dashboard.dashboard_server import DashboardServer

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = os.path.join(tmp.name, "sessions.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sessions (id TEXT, app_name TEXT, user_id TEXT, state TEXT, "
                     "create_time REAL, update_time REAL)")
        conn.execute("CREATE TABLE events (id TEXT, session_id TEXT, event_data TEXT, timestamp REAL)")
        conn.execute("INSERT INTO sessions VALUES ('s1', 'app', 'u', '{}', 1.0, 2.0)")
        for i, (author, role, text) in enumerate([("user", "user", "2+2?"), ("root", "model", "5")]):
            event = Event(invocation_id="inv-1", author=author,
                          content=types.Content(role=role, parts=[types.Part(text=text)]))
            data = json.dumps(event.model_dump(exclude_none=True, mode="json"))
            conn.execute("INSERT INTO events VALUES (?, 's1', ?, ?)", (f"e{i}", data, float(i)))
        conn.commit()
        conn.close()

        with patch("shared.utils.database_client.get_database_client", return_value=MagicMock()):
            server = DashboardServer(FastAPI(), project_root=PROJECT_ROOT)
        server.session_service_uri = f"sqlite:///{db_path}"
        with patch("shared.utils.langgraph.session_store.get_session_store") as store:
            store.return_value.get_events.return_value = []
            events = server._get_session_events("s1")
            missing = server._get_session_events("nope")

        self.assertEqual(extract_exchange(events, "inv-1"), ("2+2?", "5"))
        self.assertIsNone(missing)


if __name__ == "__main__":
    unittest.main()
