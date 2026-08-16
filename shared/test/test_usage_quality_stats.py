#!/usr/bin/env python3
"""
Unit tests for the satisfaction, latency and conversation panels.

These are the numbers a customer is shown, so the population they are computed
over matters more than the arithmetic: percentiles must ignore invocations that
never finished, background work must not be mixed into a figure that claims to
describe what a person waited for, and a satisfaction rate has to carry the size
of the sample it came from.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import AgentResponse, Base, ResponseFeedback, TokenUsageLog
from shared.utils.dashboard.dashboard_server import DashboardServer


class TestPercentile(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        self.server = DashboardServer(FastAPI(), Path(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

    def test_empty_is_zero(self):
        self.assertEqual(self.server._percentile([], 0.5), 0)

    def test_single_value(self):
        self.assertEqual(self.server._percentile([7], 0.5), 7)
        self.assertEqual(self.server._percentile([7], 0.95), 7)

    def test_nearest_rank(self):
        values = list(range(1, 101))
        self.assertEqual(self.server._percentile(values, 0.50), 50)
        self.assertEqual(self.server._percentile(values, 0.95), 95)

    def test_never_indexes_past_the_end(self):
        self.assertEqual(self.server._percentile([1, 2, 3], 1.0), 3)


class TestQualityStats(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.mock_db_client = MagicMock()
        self.mock_db_client.get_session.side_effect = lambda: self.Session()
        self.patcher = patch('shared.utils.database_client.get_database_client',
                             return_value=self.mock_db_client)
        self.patcher.start()

        from fastapi import FastAPI
        self.server = DashboardServer(FastAPI(), Path(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        self.server.db_client = self.mock_db_client

        self.now = datetime.now()
        self.start = self.now - timedelta(days=7)

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _stats(self):
        session = self.Session()
        try:
            return self.server._get_quality_stats(session, self.start, self.now)
        finally:
            session.close()

    def _response(self, duration_ms, origin="chat", minutes_ago=10, agent="a1"):
        session = self.Session()
        session.add(AgentResponse(
            invocation_id=f"i-{origin}-{duration_ms}-{minutes_ago}-{agent}",
            session_id="s1", user_id="u1", agent_name=agent, origin=origin,
            started_at=self.now - timedelta(minutes=minutes_ago),
            duration_ms=duration_ms,
            status='SUCCESS' if duration_ms is not None else 'RUNNING'))
        session.commit()
        session.close()

    def _rating(self, rating, agent="a1", message="m1"):
        session = self.Session()
        session.add(ResponseFeedback(session_id="s1", message_id=message,
                                     agent_name=agent, rating=rating,
                                     created_at=self.now - timedelta(minutes=5)))
        session.commit()
        session.close()

    def _tokens(self, session_id, prompt, response, minutes_ago=10, status='SUCCESS'):
        session = self.Session()
        session.add(TokenUsageLog(
            request_id=f"r-{session_id}-{prompt}-{minutes_ago}", session_id=session_id,
            user_id="u1", agent_name="a1", model_name="m",
            prompt_tokens=prompt, response_tokens=response, status=status,
            timestamp=self.now - timedelta(minutes=minutes_ago)))
        session.commit()
        session.close()

    # --- satisfaction ---------------------------------------------------

    def test_satisfaction_rate_and_sample_size(self):
        for i in range(3):
            self._rating("up", message=f"m{i}")
        self._rating("down", message="m9")
        for i in range(10):
            self._response(1000, minutes_ago=i + 1)

        s = self._stats()["satisfaction"]
        self.assertEqual(s["up"], 3)
        self.assertEqual(s["down"], 1)
        self.assertEqual(s["rated"], 4)
        self.assertEqual(s["rate_pct"], 75.0)
        # The denominator a reader needs: 4 ratings out of 10 answerable responses
        self.assertEqual(s["responses"], 10)

    def test_no_ratings_reports_none_not_zero(self):
        # 0% would read as "everyone hated it" rather than "nobody said"
        self._response(1000)
        s = self._stats()["satisfaction"]
        self.assertIsNone(s["rate_pct"])
        self.assertEqual(s["rated"], 0)

    def test_per_agent_breakdown(self):
        self._rating("up", agent="a1", message="m1")
        self._rating("up", agent="a1", message="m2")
        self._rating("down", agent="a2", message="m3")
        rows = {r["agent"]: r for r in self._stats()["satisfaction"]["per_agent"]}
        self.assertEqual(rows["a1"]["rate_pct"], 100.0)
        self.assertEqual(rows["a2"]["rate_pct"], 0.0)
        self.assertEqual(rows["a2"]["rated"], 1)

    # --- latency --------------------------------------------------------

    def test_percentiles_over_finished_responses(self):
        for ms in [100, 200, 300, 400, 500]:
            self._response(ms, minutes_ago=ms // 100)
        latency = self._stats()["latency"]
        self.assertEqual(latency["samples"], 5)
        self.assertEqual(latency["p50_ms"], 300)
        self.assertEqual(latency["p95_ms"], 500)

    def test_unfinished_responses_are_counted_not_averaged(self):
        # Treating a hung invocation as zero would flatter p95
        self._response(1000, minutes_ago=1)
        self._response(None, minutes_ago=2)
        self._response(None, minutes_ago=3)
        latency = self._stats()["latency"]
        self.assertEqual(latency["samples"], 1)
        self.assertEqual(latency["p50_ms"], 1000)
        self.assertEqual(latency["unfinished"], 2)

    def test_background_work_is_excluded(self):
        # A nightly trigger must not define the latency a customer reads
        self._response(500, origin="chat", minutes_ago=1)
        self._response(120000, origin="trigger", minutes_ago=2)
        self._response(90000, origin="eval", minutes_ago=3)
        latency = self._stats()["latency"]
        self.assertEqual(latency["samples"], 1)
        self.assertEqual(latency["p95_ms"], 500)

    def test_api_traffic_counts_as_interactive(self):
        self._response(400, origin="api", minutes_ago=1)
        self.assertEqual(self._stats()["latency"]["samples"], 1)

    def test_outside_the_window_is_excluded(self):
        self._response(500, minutes_ago=1)
        self._response(9999, minutes_ago=60 * 24 * 30)
        self.assertEqual(self._stats()["latency"]["samples"], 1)

    # --- conversations --------------------------------------------------

    def test_tokens_grouped_by_conversation(self):
        # Two turns of one conversation are one conversation, not two
        self._tokens("conv-1", 100, 200, minutes_ago=5)
        self._tokens("conv-1", 50, 150, minutes_ago=4)
        self._tokens("conv-2", 10, 20, minutes_ago=3)
        conversations = self._stats()["conversations"]
        self.assertEqual(conversations["count"], 2)
        # (500 + 30) / 2
        self.assertEqual(conversations["avg_tokens"], 265)

    def test_failed_calls_do_not_count_toward_conversation_cost(self):
        self._tokens("conv-1", 100, 200, minutes_ago=5)
        self._tokens("conv-1", 0, 0, minutes_ago=4, status='ERROR')
        conversations = self._stats()["conversations"]
        self.assertEqual(conversations["count"], 1)
        self.assertEqual(conversations["avg_tokens"], 300)

    def test_no_data_is_zero_not_an_error(self):
        stats = self._stats()
        self.assertEqual(stats["conversations"]["count"], 0)
        self.assertEqual(stats["latency"]["samples"], 0)
        self.assertIsNone(stats["satisfaction"]["rate_pct"])

    def test_quality_panels_reach_the_usage_stats(self):
        self._response(700, minutes_ago=1)
        self._rating("up")
        stats = self.server._get_usage_stats(days=7)
        self.assertIn("satisfaction", stats)
        self.assertIn("latency", stats)
        self.assertIn("conversations", stats)
        self.assertEqual(stats["latency"]["p50_ms"], 700)


if __name__ == '__main__':
    unittest.main()
