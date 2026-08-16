#!/usr/bin/env python3
"""
Unit tests for agent error recording.

Until this existed, a failing agent left no trace at all: token_usage_logs
declared an ERROR status that nothing wrote. Alerting on error counts reads
these rows, so a silent regression here means a broken agent pages nobody.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.callbacks.error_callback import (
    MAX_ERROR_DESCRIPTION,
    record_agent_error,
    record_model_error_callback,
)


def _callback_context(agent_name="test_agent", user_id="u1", session_id="s1"):
    """Minimal stand-in for an ADK CallbackContext."""
    invocation = SimpleNamespace(user_id=user_id, session=SimpleNamespace(id=session_id))
    ctx = SimpleNamespace(agent_name=agent_name, state={"current_model_name": "gemini-3-flash"})
    ctx._invocation_context = invocation
    return ctx


class TestRecordAgentError(unittest.TestCase):

    def test_writes_an_error_row(self):
        service = MagicMock()
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            ok = record_agent_error(agent_name="a1", user_id="u1", session_id="s1",
                                    error=ValueError("model exploded"), request_id="req-1")
        self.assertTrue(ok)
        kwargs = service.log_token_usage.call_args.kwargs
        self.assertEqual(kwargs["status"], "ERROR")
        self.assertEqual(kwargs["error_description"], "ValueError: model exploded")
        self.assertEqual(kwargs["agent_name"], "a1")
        self.assertEqual(kwargs["request_id"], "req-1")
        # Failed calls consumed no tokens; counting them as usage would skew budgets
        self.assertEqual(kwargs["prompt_tokens"], 0)
        self.assertEqual(kwargs["response_tokens"], 0)

    def test_long_description_is_truncated(self):
        service = MagicMock()
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            record_agent_error(agent_name="a1", user_id="u1", session_id="s1",
                               error=RuntimeError("x" * 5000))
        description = service.log_token_usage.call_args.kwargs["error_description"]
        self.assertEqual(len(description), MAX_ERROR_DESCRIPTION)

    def test_generates_a_request_id_when_missing(self):
        service = MagicMock()
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            record_agent_error(agent_name="a1", user_id="u1", session_id="s1",
                               error=ValueError("boom"))
        self.assertTrue(service.log_token_usage.call_args.kwargs["request_id"])

    def test_database_failure_is_swallowed(self):
        # The caller is already on a failure path — logging must never mask the real error
        service = MagicMock()
        service.log_token_usage.side_effect = RuntimeError("db down")
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            self.assertFalse(record_agent_error(agent_name="a1", user_id="u1",
                                                session_id="s1", error=ValueError("boom")))


class TestRecordModelErrorCallback(unittest.TestCase):

    def test_returns_none_so_adk_reraises(self):
        service = MagicMock()
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            result = record_model_error_callback(callback_context=_callback_context(),
                                                 llm_request=MagicMock(),
                                                 error=ValueError("boom"))
        # Returning an LlmResponse here would swallow the original error
        self.assertIsNone(result)

    def test_records_agent_model_and_session(self):
        service = MagicMock()
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            record_model_error_callback(callback_context=_callback_context(),
                                        llm_request=MagicMock(),
                                        error=ValueError("boom"))
        kwargs = service.log_token_usage.call_args.kwargs
        self.assertEqual(kwargs["agent_name"], "test_agent")
        self.assertEqual(kwargs["model_name"], "gemini-3-flash")
        self.assertEqual(kwargs["user_id"], "u1")
        self.assertEqual(kwargs["session_id"], "s1")
        self.assertEqual(kwargs["status"], "ERROR")

    def test_broken_context_does_not_raise(self):
        service = MagicMock()
        with patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service):
            result = record_model_error_callback(callback_context=object(),
                                                 llm_request=MagicMock(),
                                                 error=ValueError("boom"))
        self.assertIsNone(result)


class TestErrorCountAggregation(unittest.TestCase):
    """Real in-memory SQLite — the alert condition reads this query."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from shared.utils.models import Base, TokenUsageLog, AgentConfig

        self.TokenUsageLog = TokenUsageLog
        self.AgentConfig = AgentConfig
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        from shared.utils.token_usage_service import TokenUsageService
        self.service = TokenUsageService()
        self.service._get_session = lambda: self.Session()

        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.engine.dispose()

    def _log(self, agent_name, status, minutes_ago=1, user_id="u1"):
        session = self.Session()
        session.add(self.TokenUsageLog(
            request_id=f"r-{agent_name}-{status}-{minutes_ago}", session_id="s1",
            user_id=user_id, agent_name=agent_name, model_name="m",
            prompt_tokens=0, response_tokens=0, status=status,
            timestamp=self.now - timedelta(minutes=minutes_ago)))
        session.commit()
        session.close()

    def test_counts_only_error_rows(self):
        self._log("a1", "ERROR")
        self._log("a1", "ERROR", minutes_ago=2)
        self._log("a1", "SUCCESS")
        # An RBAC denial is not a model failure and must not trip an error alert
        self._log("a1", "ACCESS_DENIED")
        count = self.service.get_error_count_since(self.now - timedelta(minutes=10), agent_name="a1")
        self.assertEqual(count, 2)

    def test_respects_the_window(self):
        self._log("a1", "ERROR", minutes_ago=1)
        self._log("a1", "ERROR", minutes_ago=90)
        self.assertEqual(
            self.service.get_error_count_since(self.now - timedelta(minutes=15), agent_name="a1"), 1)

    def test_scopes_by_agent(self):
        self._log("a1", "ERROR")
        self._log("a2", "ERROR")
        self.assertEqual(
            self.service.get_error_count_since(self.now - timedelta(minutes=10), agent_name="a1"), 1)

    def test_scopes_by_project_through_its_agents(self):
        session = self.Session()
        session.add(self.AgentConfig(name="a1", type="llm", project_id=7))
        session.add(self.AgentConfig(name="a2", type="llm", project_id=8))
        session.commit()
        session.close()
        self._log("a1", "ERROR")
        self._log("a2", "ERROR")
        self.assertEqual(
            self.service.get_error_count_since(self.now - timedelta(minutes=10), project_id=7), 1)

    def test_unknown_project_counts_nothing(self):
        self._log("a1", "ERROR")
        self.assertEqual(
            self.service.get_error_count_since(self.now - timedelta(minutes=10), project_id=999), 0)

    def test_returns_zero_without_a_database(self):
        self.service._get_session = lambda: None
        self.assertEqual(self.service.get_error_count_since(self.now), 0)


if __name__ == '__main__':
    unittest.main()
