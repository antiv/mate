#!/usr/bin/env python3
"""
Usage analytics must count successful LLM traffic only.

Failures and RBAC denials are stored in the same table as successful calls, with
zero tokens. Counting them alongside token sums silently inflates request counts
and drags every per-request average toward zero — the regression that recording
agent errors would otherwise have introduced.
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

from shared.utils.models import Base, TokenUsageLog
from shared.utils.dashboard.dashboard_server import DashboardServer


class TestUsageStatsExcludeFailures(unittest.TestCase):

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

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _log(self, status, agent_name="a1", prompt=10, response=20, user_id="u1"):
        session = self.Session()
        session.add(TokenUsageLog(
            request_id=f"r-{status}-{agent_name}-{user_id}-{prompt}", session_id="s1",
            user_id=user_id, agent_name=agent_name, model_name="m",
            prompt_tokens=prompt, response_tokens=response, status=status,
            timestamp=datetime.now() - timedelta(hours=1)))
        session.commit()
        session.close()

    def test_error_rows_do_not_inflate_request_counts(self):
        self._log("SUCCESS")
        self._log("ERROR", prompt=0, response=0)
        self._log("ERROR", prompt=0, response=0, user_id="u2")
        stats = self.server._get_usage_stats(days=7)
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["total_prompt_tokens"], 10)

    def test_access_denied_rows_are_excluded_too(self):
        self._log("SUCCESS")
        self._log("ACCESS_DENIED", prompt=0, response=0)
        self.assertEqual(self.server._get_usage_stats(days=7)["total_requests"], 1)

    def test_failures_do_not_add_unique_users_or_agents(self):
        self._log("SUCCESS", agent_name="a1", user_id="u1")
        self._log("ERROR", agent_name="ghost_agent", user_id="ghost_user", prompt=0, response=0)
        stats = self.server._get_usage_stats(days=7)
        self.assertEqual(stats["unique_users"], 1)
        self.assertEqual(stats["unique_agents"], 1)

    def test_top_agents_ranks_on_successful_calls(self):
        self._log("SUCCESS", agent_name="busy")
        self._log("SUCCESS", agent_name="busy", prompt=11)
        for i in range(5):
            self._log("ERROR", agent_name="broken", prompt=0, response=i)
        top = {row["agent"]: row["requests"] for row in self.server._get_usage_stats(days=7)["top_agents"]}
        self.assertEqual(top.get("busy"), 2)
        self.assertNotIn("broken", top)

    def test_daily_and_hourly_series_exclude_failures(self):
        self._log("SUCCESS")
        self._log("ERROR", prompt=0, response=0)
        stats = self.server._get_usage_stats(days=7)
        self.assertEqual(sum(d["requests"] for d in stats["daily_usage"]), 1)
        self.assertEqual(sum(stats["hourly_usage"]), 1)


if __name__ == '__main__':
    unittest.main()
