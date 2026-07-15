#!/usr/bin/env python3
"""
Unit tests for the LangGraph session store: the ADK-compatible session wire
shape, explicit-id creation (openai_routes contract), 404/None semantics,
list ordering and event/state persistence.
"""

import unittest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.utils.models import Base
from shared.utils.langgraph.session_store import SessionStore


class SessionStoreTestCase(unittest.TestCase):

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine)

        fake_client = Mock()
        fake_client.get_session.side_effect = lambda: self._session_factory()
        self._patcher = patch("shared.utils.langgraph.session_store.get_database_client",
                              return_value=fake_client)
        self._patcher.start()
        self.store = SessionStore()

    def tearDown(self):
        self._patcher.stop()

    def test_create_returns_adk_wire_shape(self):
        session = self.store.create_session("my_app", "u1")
        for key in ("id", "appName", "app_name", "userId", "user_id",
                    "state", "events", "lastUpdateTime", "last_update_time"):
            self.assertIn(key, session)
        self.assertEqual(session["appName"], "my_app")
        self.assertEqual(session["events"], [])

    def test_create_with_explicit_id_and_duplicate(self):
        created = self.store.create_session("my_app", "u1", session_id="fixed-id")
        self.assertEqual(created["id"], "fixed-id")
        # duplicate → None (endpoint maps this to HTTP 400, matching ADK)
        self.assertIsNone(self.store.create_session("my_app", "u1", session_id="fixed-id"))

    def test_get_missing_session_returns_none(self):
        self.assertIsNone(self.store.get_session("my_app", "u1", "ghost"))

    def test_get_scopes_by_app_and_user(self):
        session = self.store.create_session("my_app", "u1")
        self.assertIsNone(self.store.get_session("other_app", "u1", session["id"]))
        self.assertIsNone(self.store.get_session("my_app", "other_user", session["id"]))
        self.assertIsNotNone(self.store.get_session("my_app", "u1", session["id"]))

    def test_events_persist_in_adk_shape(self):
        session = self.store.create_session("my_app", "u1")
        sid = session["id"]
        self.store.append_event(sid, {
            "id": "ev1", "author": "user", "invocationId": "inv1",
            "content": {"role": "user", "parts": [{"text": "zdravo"}]},
            "timestamp": 1000.0,
        })
        self.store.append_event(sid, {
            "id": "ev2", "author": "my_app", "invocationId": "inv1",
            "content": {"role": "model", "parts": [{"text": "zdravo!"}]},
            "actions": {"artifactDelta": {"img.png": 0}},
            "usageMetadata": {"prompt_token_count": 5},
            "timestamp": 1001.0,
        })
        loaded = self.store.get_session("my_app", "u1", sid)
        self.assertEqual(len(loaded["events"]), 2)
        first, second = loaded["events"]
        self.assertEqual(first["author"], "user")
        self.assertEqual(first["content"]["parts"][0]["text"], "zdravo")
        self.assertEqual(second["actions"]["artifactDelta"], {"img.png": 0})
        self.assertEqual(second["usageMetadata"]["prompt_token_count"], 5)

    def test_list_sessions_only_for_app_user(self):
        self.store.create_session("my_app", "u1")
        self.store.create_session("my_app", "u1")
        self.store.create_session("my_app", "u2")
        self.store.create_session("other_app", "u1")
        sessions = self.store.list_sessions("my_app", "u1")
        self.assertEqual(len(sessions), 2)

    def test_delete_session(self):
        session = self.store.create_session("my_app", "u1")
        self.assertTrue(self.store.delete_session("my_app", "u1", session["id"]))
        self.assertFalse(self.store.delete_session("my_app", "u1", session["id"]))
        self.assertIsNone(self.store.get_session("my_app", "u1", session["id"]))

    def test_state_merge(self):
        session = self.store.create_session("my_app", "u1", state={"a": 1})
        sid = session["id"]
        self.store.update_state(sid, {"b": 2})
        self.store.update_state(sid, {"a": 3})
        self.assertEqual(self.store.get_state(sid), {"a": 3, "b": 2})

    def test_session_exists(self):
        session = self.store.create_session("my_app", "u1")
        self.assertTrue(self.store.session_exists("my_app", "u1", session["id"]))
        self.assertFalse(self.store.session_exists("my_app", "u1", "ghost"))


if __name__ == "__main__":
    unittest.main()
