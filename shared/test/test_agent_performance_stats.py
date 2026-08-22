#!/usr/bin/env python3
"""
The agent performance table must report measurements, not placeholders.

Avg. response time, success rate and last used were hardcoded literals in the
template, printed identically for every agent. Each one now has to come from a
row, and an agent with nothing recorded has to arrive as None so the page can
say "no data" instead of showing a number nobody measured.
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

from shared.utils.models import AgentResponse, Base, TokenUsageLog
from shared.utils.dashboard.dashboard_server import DashboardServer


class TestAgentPerformanceStats(unittest.TestCase):

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
        project_root = Path(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        self.server = DashboardServer(FastAPI(), project_root)
        self.server.db_client = self.mock_db_client
        self._seq = 0

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _log(self, agent_name="a1", hours_ago=1, status="SUCCESS"):
        """One token_usage_logs row — what the Requests column counts."""
        self._seq += 1
        session = self.Session()
        session.add(TokenUsageLog(
            request_id=f"r-{self._seq}", session_id="s1", user_id="u1",
            agent_name=agent_name, model_name="m", prompt_tokens=10,
            response_tokens=20, status=status,
            timestamp=datetime.now() - timedelta(hours=hours_ago)))
        session.commit()
        session.close()

    def _invocation(self, agent_name="a1", duration_ms=1000, status="SUCCESS",
                    hours_ago=1, origin="chat"):
        """One agent_responses row — what duration and success rate come from."""
        self._seq += 1
        session = self.Session()
        session.add(AgentResponse(
            invocation_id=f"i-{self._seq}", session_id="s1", user_id="u1",
            agent_name=agent_name, origin=origin, duration_ms=duration_ms,
            status=status, started_at=datetime.now() - timedelta(hours=hours_ago)))
        session.commit()
        session.close()

    def _rows(self, days=7):
        return {r["agent"]: r for r in
                self.server._get_usage_stats(days=days)["top_agents"]}

    # --- Last used ------------------------------------------------------

    def test_last_used_is_the_agents_own_newest_call(self):
        self._log(agent_name="fresh", hours_ago=1)
        self._log(agent_name="fresh", hours_ago=40)
        self._log(agent_name="stale", hours_ago=30)
        rows = self._rows()
        self.assertEqual(rows["fresh"]["last_used_label"], "1h ago")
        self.assertEqual(rows["stale"]["last_used_label"], "1d ago")

    def test_last_used_differs_between_agents(self):
        """The reported bug: every row showed the same hardcoded '2 hours ago'."""
        for hours in (1, 5, 26):
            self._log(agent_name=f"agent_{hours}", hours_ago=hours)
        labels = [r["last_used_label"] for r in self._rows().values()]
        self.assertEqual(len(set(labels)), 3, f"expected distinct labels, got {labels}")

    def test_last_used_carries_an_exact_timestamp_too(self):
        self._log(agent_name="a1", hours_ago=2)
        row = self._rows()["a1"]
        self.assertIsNotNone(row["last_used"])
        # ISO string, parseable by the template's title attribute and the sorter
        self.assertLess(abs((datetime.fromisoformat(row["last_used"])
                             - datetime.now()).total_seconds() + 7200), 60)

    # --- Duration and success rate --------------------------------------

    def test_avg_response_time_averages_recorded_durations(self):
        self._log(agent_name="a1")
        self._invocation(agent_name="a1", duration_ms=1000)
        self._invocation(agent_name="a1", duration_ms=2000)
        row = self._rows()["a1"]
        self.assertEqual(row["avg_response_ms"], 1500)
        self.assertEqual(row["avg_response_label"], "1.5s")

    def test_success_rate_counts_failed_invocations(self):
        self._log(agent_name="a1")
        for _ in range(3):
            self._invocation(agent_name="a1", status="SUCCESS")
        self._invocation(agent_name="a1", status="ERROR")
        self.assertEqual(self._rows()["a1"]["success_rate_pct"], 75.0)

    def test_unfinished_invocations_lower_the_rate_but_not_the_average(self):
        """A NULL duration means the run never closed: an error, not a fast reply."""
        self._log(agent_name="a1")
        self._invocation(agent_name="a1", duration_ms=1000, status="SUCCESS")
        self._invocation(agent_name="a1", duration_ms=None, status="ERROR")
        row = self._rows()["a1"]
        self.assertEqual(row["avg_response_ms"], 1000)
        self.assertEqual(row["success_rate_pct"], 50.0)
        self.assertEqual(row["invocations"], 2)

    def test_metrics_do_not_bleed_between_agents(self):
        self._log(agent_name="slow")
        self._log(agent_name="fast")
        self._invocation(agent_name="slow", duration_ms=4000)
        self._invocation(agent_name="fast", duration_ms=200)
        rows = self._rows()
        self.assertEqual(rows["slow"]["avg_response_label"], "4.0s")
        self.assertEqual(rows["fast"]["avg_response_label"], "200ms")

    def test_invocations_outside_the_window_are_ignored(self):
        self._log(agent_name="a1", hours_ago=1)
        self._invocation(agent_name="a1", duration_ms=9000, hours_ago=24 * 30)
        row = self._rows(days=7)
        self.assertIsNone(row["a1"]["avg_response_ms"])

    def test_every_origin_counts_not_just_interactive(self):
        """Requests beside it counts all origins; filtering only these would
        put inconsistent numbers on one row."""
        self._log(agent_name="a1")
        self._invocation(agent_name="a1", duration_ms=1000, origin="trigger")
        self.assertEqual(self._rows()["a1"]["avg_response_ms"], 1000)

    # --- Absent data ----------------------------------------------------

    def test_agent_without_invocations_reports_no_data(self):
        """The whole point: no rows means None, never a fabricated 1.2s / 98.5%."""
        self._log(agent_name="untracked")
        row = self._rows()["untracked"]
        self.assertEqual(row["requests"], 1)
        self.assertIsNone(row["avg_response_ms"])
        self.assertIsNone(row["avg_response_label"])
        self.assertIsNone(row["success_rate_pct"])
        self.assertEqual(row["invocations"], 0)

    def test_existing_agent_and_requests_keys_are_unchanged(self):
        self._log(agent_name="a1")
        self._log(agent_name="a1")
        row = self._rows()["a1"]
        self.assertEqual(row["agent"], "a1")
        self.assertEqual(row["requests"], 2)

    def test_missing_agent_responses_table_leaves_the_page_standing(self):
        self._log(agent_name="a1")
        with patch.object(DashboardServer, '_get_agent_perf', return_value={}):
            row = self._rows()["a1"]
        self.assertEqual(row["requests"], 1)
        self.assertIsNone(row["success_rate_pct"])
        self.assertIsNotNone(row["last_used_label"])


class TestPerformanceFormatters(unittest.TestCase):

    NOW = datetime(2026, 1, 10, 12, 0, 0)

    def test_relative_time_buckets(self):
        cases = [
            (timedelta(seconds=30), 'just now'),
            (timedelta(minutes=1), '1m ago'),
            (timedelta(minutes=59), '59m ago'),
            (timedelta(minutes=60), '1h ago'),
            (timedelta(hours=23, minutes=59), '23h ago'),
            (timedelta(days=1), '1d ago'),
            (timedelta(days=45), '45d ago'),
        ]
        for delta, expected in cases:
            with self.subTest(delta=delta):
                self.assertEqual(
                    DashboardServer._format_relative(self.NOW - delta, self.NOW),
                    expected)

    def test_relative_time_of_nothing_is_nothing(self):
        self.assertIsNone(DashboardServer._format_relative(None, self.NOW))

    def test_duration_switches_unit_at_one_second(self):
        self.assertEqual(DashboardServer._format_duration_ms(0), '0ms')
        self.assertEqual(DashboardServer._format_duration_ms(999), '999ms')
        self.assertEqual(DashboardServer._format_duration_ms(1000), '1.0s')
        self.assertEqual(DashboardServer._format_duration_ms(1250), '1.2s')

    def test_missing_duration_stays_missing(self):
        """None must not collapse to 0ms — that would read as an instant reply."""
        self.assertIsNone(DashboardServer._format_duration_ms(None))


if __name__ == '__main__':
    unittest.main()
