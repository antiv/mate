#!/usr/bin/env python3
"""
The usage page is open to non-admins as "My Usage", and must show them only their
own traffic. It rendered everyone's: totals, top agents, cost, ratings and, with
?view=logs, every user's token log rows with their user IDs.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.dashboard.dashboard_server import DashboardServer
from shared.utils.models import AgentResponse, Base, ResponseFeedback, TokenUsageLog
from shared.test.request_context_helpers import as_caller


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _TwoUsers(unittest.TestCase):
    """alice used alice_agent, bob used bob_agent; each rated one reply."""

    def setUp(self):
        # One shared connection: TestClient serves the page from another thread
        self.engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = MagicMock()
        db.get_session.side_effect = lambda: self.Session()
        p = patch('shared.utils.database_client.get_database_client', return_value=db)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.engine.dispose)
        root = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.app = FastAPI()
        self.app.add_middleware(SessionMiddleware, secret_key="test")
        self.server = DashboardServer(self.app, root)
        self.server.db_client = db

        session = self.Session()
        now = _utcnow()
        for user, prompt in (("alice", 10), ("bob", 1000)):
            session.add(TokenUsageLog(
                request_id=f"r-{user}", session_id=f"s-{user}", user_id=user,
                agent_name=f"{user}_agent", model_name="m", prompt_tokens=prompt,
                response_tokens=prompt, status="SUCCESS", cost_usd=prompt / 1000,
                timestamp=now - timedelta(hours=1)))
            session.add(AgentResponse(
                invocation_id=f"i-{user}", session_id=f"s-{user}", user_id=user,
                agent_name=f"{user}_agent", origin="chat",
                started_at=now - timedelta(hours=1), duration_ms=prompt, status="SUCCESS"))
            session.add(ResponseFeedback(
                session_id=f"s-{user}", message_id=f"m-{user}", agent_name=f"{user}_agent",
                rating="up" if user == "alice" else "down",
                created_at=now - timedelta(minutes=30)))
        session.commit()
        session.close()


class TestScopedHelpers(_TwoUsers):

    def test_stats_for_one_user_leave_the_other_out(self):
        stats = self.server._get_usage_stats(days=7, user_id="alice")
        self.assertEqual(stats["total_requests"], 1)
        self.assertEqual(stats["total_prompt_tokens"], 10)
        self.assertEqual([a["agent"] for a in stats["top_agents"]], ["alice_agent"])
        self.assertEqual([a["agent"] for a in stats["cost"]["by_agent"]], ["alice_agent"])
        self.assertAlmostEqual(stats["cost"]["total_usd"], 0.01)
        self.assertEqual((stats["satisfaction"]["up"], stats["satisfaction"]["down"]), (1, 0))
        self.assertEqual(stats["satisfaction"]["responses"], 1)
        self.assertEqual(stats["latency"]["samples"], 1)
        self.assertEqual(stats["conversations"]["count"], 1)

    def test_stats_without_a_user_cover_everyone(self):
        stats = self.server._get_usage_stats(days=7)
        self.assertEqual(stats["total_requests"], 2)
        self.assertEqual(stats["unique_users"], 2)

    def test_logs_for_one_user_leave_the_other_out(self):
        logs = self.server._get_token_usage_logs(24, limit=100, user_id="alice")
        self.assertEqual([log["user_id"] for log in logs["logs"]], ["alice"])
        self.assertEqual(len(self.server._get_token_usage_logs(24, limit=100)["logs"]), 2)


class TestUsagePage(_TwoUsers):

    def get(self, user: str, is_admin: bool, query: str = "") -> str:
        as_caller(self.app, user, is_admin)
        resp = TestClient(self.app).get(f"/dashboard/usage{query}")
        self.assertEqual(resp.status_code, 200)
        return resp.text

    def test_non_admin_sees_only_their_own_usage(self):
        for query in ("", "?view=logs"):
            page = self.get("alice", is_admin=False, query=query)
            self.assertIn("alice_agent", page, query)
            self.assertNotIn("bob_agent", page, query)
            self.assertNotIn("s-bob", page, query)

    def test_admin_still_sees_everyone(self):
        page = self.get("admin", is_admin=True, query="?view=logs")
        self.assertIn("alice_agent", page)
        self.assertIn("bob_agent", page)


if __name__ == "__main__":
    unittest.main()
