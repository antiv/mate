#!/usr/bin/env python3
"""
Tests for the daily usage series behind both dashboard charts.

The overview ("Last 7 Days") and the usage analytics page ("Daily Usage Trend")
plot the same `daily_usage` field over different windows, and appeared to
disagree about the same database. Three defects were responsible:

1. The window was built with naive `datetime.now()` while rows are stored in
   UTC, so on any non-UTC host the range silently shifted by the offset.
2. `start_date` was a mid-day timestamp, so the oldest bucket was a partial day
   and always rendered as a dip at the left edge — a trend that was not there.
3. Days with no traffic were omitted. Charts space points evenly, so gaps
   collapsed and the x-axis stopped being a timeline. Two windows compress
   their gaps differently, which is what made the charts disagree.
"""

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import Base, TokenUsageLog


class TestDailyUsageSeries(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.db_client = MagicMock()
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.patcher = patch("shared.utils.database_client.get_database_client",
                             return_value=self.db_client)
        self.patcher.start()

        from fastapi import FastAPI
        from shared.utils.dashboard.dashboard_server import DashboardServer

        project_root = Path(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        self.server = DashboardServer(FastAPI(), project_root)
        self.server.db_client = self.db_client
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _seed(self, entries):
        """entries: iterable of (days_ago, count)."""
        session = self.Session()
        for days_ago, count in entries:
            for i in range(count):
                session.add(TokenUsageLog(
                    request_id=f"r{days_ago}-{i}", agent_name="a1", status="SUCCESS",
                    prompt_tokens=100, response_tokens=50,
                    timestamp=self.now - timedelta(days=days_ago, minutes=i)))
        session.commit()
        session.close()

    def test_series_has_one_point_per_day_in_the_window(self):
        self._seed([(25, 4), (18, 9), (2, 6)])
        series = self.server._get_usage_stats(30)["daily_usage"]
        self.assertEqual(len(series), 30)

    def test_quiet_days_appear_as_zero_not_as_gaps(self):
        self._seed([(5, 3)])
        series = self.server._get_usage_stats(7)["daily_usage"]
        self.assertEqual(len(series), 7)
        self.assertEqual(sum(1 for d in series if d["requests"] == 0), 6)
        self.assertEqual([d["requests"] for d in series if d["requests"]], [3])

    def test_dates_are_consecutive_and_end_today(self):
        # The x-axis must be a real timeline, not a list of days that had traffic.
        self._seed([(3, 2)])
        series = self.server._get_usage_stats(7)["daily_usage"]
        dates = [datetime.fromisoformat(d["date"]).date() for d in series]
        self.assertEqual(dates, sorted(dates))
        for earlier, later in zip(dates, dates[1:]):
            self.assertEqual((later - earlier).days, 1)
        self.assertEqual(dates[-1], self.now.date())

    def test_the_oldest_bucket_is_a_whole_day(self):
        # Seed the full first day. A mid-day start would only count part of it.
        oldest = 6
        self._seed([(oldest, 24)])
        series = self.server._get_usage_stats(7)["daily_usage"]
        self.assertEqual(series[0]["requests"], 24)

    def test_totals_do_not_move_with_the_host_timezone(self):
        # Rows are UTC; a naive local window shifted the range by the host offset.
        # The data has to be dense right up to the window edge for the shift to
        # show: sparse rows sitting far from the boundary survive a two-hour
        # slide unchanged, and the test would pass against the broken code.
        session = self.Session()
        for hour in range(9 * 24):
            session.add(TokenUsageLog(
                request_id=f"h{hour}", agent_name="a1", status="SUCCESS",
                prompt_tokens=10, response_tokens=5,
                timestamp=self.now - timedelta(hours=hour)))
        session.commit()
        session.close()

        if not hasattr(time, "tzset"):
            self.skipTest("tzset is unavailable on this platform")

        def totals_under(tz):
            # os.environ must be assigned directly: patching it through
            # mock.patch.dict does not reach putenv, so tzset() reads the old
            # zone and the test silently stops exercising anything.
            os.environ["TZ"] = tz
            time.tzset()
            self.assertEqual(datetime.now().hour, datetime.now(ZoneInfo(tz)).hour,
                             f"TZ={tz} did not take effect; the check below is vacuous")
            return self.server._get_usage_stats(7)["total_requests"]

        original_tz = os.environ.get("TZ")
        try:
            utc = totals_under("UTC")
            plus_two = totals_under("Europe/Belgrade")
            minus_eight = totals_under("America/Los_Angeles")
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

        self.assertEqual(utc, plus_two)
        self.assertEqual(utc, minus_eight)

    def test_daily_requests_sum_to_the_headline_total(self):
        # The chart and the number above it must not tell different stories.
        self._seed([(1, 4), (3, 7)])
        stats = self.server._get_usage_stats(7)
        self.assertEqual(sum(d["requests"] for d in stats["daily_usage"]),
                         stats["total_requests"])

    def test_failures_stay_out_of_the_series(self):
        self._seed([(1, 3)])
        session = self.Session()
        session.add(TokenUsageLog(request_id="denied", agent_name="a1",
                                  status="ACCESS_DENIED", prompt_tokens=0,
                                  response_tokens=0,
                                  timestamp=self.now - timedelta(days=1)))
        session.commit()
        session.close()
        series = self.server._get_usage_stats(7)["daily_usage"]
        self.assertEqual(sum(d["requests"] for d in series), 3)

    def test_empty_database_still_yields_a_full_series(self):
        series = self.server._get_usage_stats(7)["daily_usage"]
        self.assertEqual(len(series), 7)
        self.assertTrue(all(d["requests"] == 0 and d["tokens"] == 0 for d in series))


if __name__ == "__main__":
    unittest.main()
