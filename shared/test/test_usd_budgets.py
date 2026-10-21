#!/usr/bin/env python3
"""
Tests for budgets and budget alerts in US dollars.

A dollar budget (Rate Limits) or a dollar budget alert compares a user's,
agent's or project's spend, summed from token_usage_logs.cost_usd, against a
limit. Calls whose model has no price (NULL cost) add nothing, unless the
budget treats them as over budget. Each budget is measured in its own scope: a
user's budget against that user's spend only; an agent's can include its
sub-agents. A block budget whose spend cannot be read refuses the request.
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.test.test_alert_rules import _AlertTestCase
from shared.utils.models import AgentConfig, AlertRule, Base, Project, RateLimitConfig, TokenUsageLog
from shared.utils.rate_limit_service import RateLimitService
from shared.utils.token_usage_service import TokenUsageService
from shared.test.request_context_helpers import as_caller


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with patch("shared.utils.token_usage_service.get_database_client", return_value=MagicMock()):
            self.tokens = TokenUsageService()
        self.tokens._get_session = lambda: self.Session()
        session = self.Session()
        session.add(Project(id=5, name="Shop"))
        session.add(AgentConfig(name="seller", type="llm", project_id=5))
        session.add(AgentConfig(name="helper", type="llm", project_id=5))
        session.commit()
        session.close()

    def _spend(self, cost, user="u1", agent="seller", hours_ago=1):
        session = self.Session()
        session.add(TokenUsageLog(request_id="r", user_id=user, agent_name=agent, model_name="m",
                                  prompt_tokens=10, response_tokens=10, cost_usd=cost,
                                  timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago)))
        session.commit()
        session.close()


class TestSpendSince(_Db):

    def test_spend_per_user_agent_and_project(self):
        self._spend(1.0, user="u1", agent="seller")
        self._spend(0.5, user="u2", agent="helper")
        self._spend(None, user="u1", agent="seller")  # no price: adds nothing, counted as unpriced
        self._spend(9.0, user="u1", agent="seller", hours_ago=48)
        since = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(self.tokens.get_spend_since("user", "u1", since), (1.0, 1))
        self.assertEqual(self.tokens.get_spend_since("agent", "helper", since), (0.5, 0))
        self.assertEqual(self.tokens.get_spend_since("project", "5", since), (1.5, 1))
        self.assertEqual(self.tokens.get_spend_since("project", "not-a-number", since), (0.0, 0))
        self.assertEqual(self.tokens.get_spend_since("global", None, since), (0.0, 0))

    def test_failed_calls_are_not_unpriced(self):
        session = self.Session()
        session.add(TokenUsageLog(request_id="r", user_id="u1", agent_name="seller", status="ERROR",
                                  timestamp=datetime.now(timezone.utc)))
        session.commit()
        session.close()
        since = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(self.tokens.get_spend_since("agent", "seller", since), (0.0, 0))

    def test_sub_agents_on_request(self):
        session = self.Session()
        session.query(AgentConfig).filter(AgentConfig.name == "helper").one().set_parent_agents(["seller"])
        session.add(AgentConfig(name="deep", type="llm", parent_agents='["helper"]'))
        session.commit()
        session.close()
        self._spend(1.0, agent="seller")
        self._spend(0.5, agent="helper")
        self._spend(None, agent="deep")
        since = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(self.tokens.get_spend_since("agent", "seller", since), (1.0, 0))
        self.assertEqual(self.tokens.get_spend_since("agent", "seller", since, include_sub_agents=True),
                         (1.5, 1))
        self.assertEqual(self.tokens.get_agent_tokens_since("seller", since, include_sub_agents=True), 60)

    def test_a_database_error_is_none_not_zero(self):
        from sqlalchemy.exc import OperationalError
        broken = MagicMock()
        broken.query.side_effect = OperationalError("SELECT", {}, Exception("gone"))
        self.tokens._get_session = lambda: broken
        with self.assertLogs("shared.utils.token_usage_service", "ERROR"):
            self.assertIsNone(self.tokens.get_spend_since(
                "user", "u1", datetime.now(timezone.utc) - timedelta(days=1)))


class TestRateLimitBudget(_Db):

    def setUp(self):
        super().setUp()
        with patch("shared.utils.rate_limit_service.get_database_client", return_value=MagicMock()), \
                patch("shared.utils.rate_limit_service.get_token_usage_service", return_value=self.tokens):
            self.service = RateLimitService()
        self.service._get_session = lambda: self.Session()
        self.service.token_service = self.tokens

    def _config(self, **kwargs):
        session = self.Session()
        session.add(RateLimitConfig(**kwargs))
        session.commit()
        session.close()

    def _check(self, user="u1", agent="seller"):
        return asyncio.run(self.service.check_request_limit(user_id=user, agent_name=agent))[0]

    def test_a_dollar_budget_blocks_once_spent(self):
        self._config(scope="agent", scope_id="seller", usd_per_day=1.0, action_on_limit="block")
        self._spend(0.6)
        self.assertTrue(self._check().allowed)
        self._spend(0.6)
        result = self._check()
        self.assertFalse(result.allowed)
        self.assertIn("$1.20 spent in the last 24 hours (limit: $1.00)", result.message)

    def test_warn_logs_and_lets_the_request_through(self):
        self._config(scope="agent", scope_id="seller", usd_per_month=1.0, action_on_limit="warn")
        self._spend(5.0, hours_ago=24 * 10)
        with self.assertLogs("shared.utils.rate_limit_service", "WARNING"):
            self.assertTrue(self._check().allowed)

    def test_each_budget_is_measured_in_its_own_scope(self):
        # u1's budget is not used up by what other people spent on the same agent
        self._config(scope="user", scope_id="u1", usd_per_day=1.0, action_on_limit="block")
        self._spend(5.0, user="u2")
        self.assertTrue(self._check(user="u1").allowed)
        self._config(scope="project", scope_id="5", usd_per_day=1.0, action_on_limit="block")
        self.assertFalse(self._check(user="u1").allowed, "the project's budget counts every agent in it")

    def test_calls_without_a_price_do_not_count(self):
        self._config(scope="agent", scope_id="seller", usd_per_day=0.5, action_on_limit="block")
        for _ in range(5):
            self._spend(None)
        self.assertTrue(self._check().allowed)

    def test_unpriced_calls_can_count_as_over_budget(self):
        self._config(scope="agent", scope_id="seller", usd_per_day=5.0, action_on_limit="block",
                     block_unpriced=True)
        self._spend(0.1)
        self.assertTrue(self._check().allowed)
        self._spend(None)
        result = self._check()
        self.assertFalse(result.allowed)
        self.assertIn("1 calls without a price in the last 24 hours count as over the $5.00 budget",
                      result.message)

    def test_a_block_budget_fails_closed_when_spend_cannot_be_read(self):
        self._config(scope="agent", scope_id="seller", usd_per_day=5.0, action_on_limit="block")
        self.tokens.get_spend_since = MagicMock(return_value=None)
        result = self._check()
        self.assertFalse(result.allowed)
        self.assertIn("could not be read", result.message)

    def test_a_warn_budget_lets_the_request_through_when_spend_cannot_be_read(self):
        self._config(scope="agent", scope_id="seller", usd_per_day=5.0, action_on_limit="warn")
        self.tokens.get_spend_since = MagicMock(return_value=None)
        with self.assertLogs("shared.utils.rate_limit_service", "WARNING"):
            self.assertTrue(self._check().allowed)

    def test_an_agent_budget_can_include_its_sub_agents(self):
        session = self.Session()
        session.query(AgentConfig).filter(AgentConfig.name == "helper").one().set_parent_agents(["seller"])
        session.commit()
        session.close()
        self._config(scope="agent", scope_id="seller", usd_per_day=1.0, tokens_per_day=50,
                     action_on_limit="block")
        self._spend(2.0, user="u2", agent="helper")
        self._spend(2.0, user="u2", agent="helper")
        self._spend(2.0, user="u2", agent="helper")
        self.assertTrue(self._check().allowed, "without the switch only the agent's own calls count")
        session = self.Session()
        session.query(RateLimitConfig).one().include_sub_agents = True
        session.commit()
        session.close()
        result = self._check()
        self.assertFalse(result.allowed)
        self.assertIn("tokens today", result.message, "the token budget comes first and sees them too")
        session = self.Session()
        session.query(RateLimitConfig).one().tokens_per_day = None
        session.commit()
        session.close()
        self.assertIn("$6.00 spent", self._check().message)

    def test_the_usage_panel_shows_the_spend(self):
        self._spend(0.25)
        self._spend(1.0, hours_ago=24 * 3)
        usage = self.service.get_usage_snapshot(agent_name="seller")
        self.assertAlmostEqual(usage.usd_last_day, 0.25)
        self.assertAlmostEqual(usage.usd_last_month, 1.25)

    def test_the_usage_panel_counts_unpriced_calls(self):
        self._spend(None)
        self._spend(None, hours_ago=24 * 3)
        usage = self.service.get_usage_snapshot(agent_name="seller")
        self.assertEqual((usage.unpriced_calls_day, usage.unpriced_calls_month), (1, 2))


class TestBudgetAlertInDollars(_AlertTestCase):

    def _fire(self):
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            fired = self.service.evaluate_all()
        return fired, post

    def test_a_dollar_limit_on_the_rule(self):
        self.token_service.get_spend_since.return_value = (9.5, 0)
        self._rule(condition_type="budget_threshold",
                   condition_config={"unit": "usd", "threshold_pct": 90, "period": "day", "usd_limit": 10})
        fired, post = self._fire()
        self.assertEqual(len(fired), 1)
        payload = post.call_args.args[1]
        self.assertEqual(payload["event"], "rate_limit_alert")
        self.assertEqual(payload["value"], 95)
        self.assertEqual(payload["message"], "agent a1 has used 95% of its day budget ($9.50 of $10.00)")
        self.assertEqual(self.token_service.get_spend_since.call_args.args[:2], ("agent", "a1"))

    def test_the_message_says_how_many_calls_have_no_price(self):
        self.token_service.get_spend_since.return_value = (9.5, 3)
        self._rule(condition_type="budget_threshold",
                   condition_config={"unit": "usd", "threshold_pct": 90, "period": "day", "usd_limit": 10})
        fired, post = self._fire()
        self.assertEqual(post.call_args.args[1]["message"],
                         "agent a1 has used 95% of its day budget ($9.50 of $10.00); "
                         "3 calls without a price are not counted")

    def test_no_alert_when_the_spend_cannot_be_read(self):
        self.token_service.get_spend_since.return_value = None
        self._rule(condition_type="budget_threshold",
                   condition_config={"unit": "usd", "threshold_pct": 0, "period": "day", "usd_limit": 10})
        fired, post = self._fire()
        self.assertEqual(fired, [])

    def test_the_limit_falls_back_to_the_rate_limits_dollar_budget(self):
        session = self.Session()
        session.add(RateLimitConfig(scope="agent", scope_id="a1", usd_per_month=20.0, tokens_per_day=5))
        session.commit()
        session.close()
        self.token_service.get_spend_since.return_value = (19.0, 0)
        self._rule(condition_type="budget_threshold",
                   condition_config={"unit": "usd", "threshold_pct": 90, "period": "month"})
        fired, post = self._fire()
        self.assertEqual(len(fired), 1)
        self.assertEqual(post.call_args.args[1]["limit"], 20.0)

    def test_without_a_dollar_limit_or_per_hour_it_is_inert(self):
        self.token_service.get_spend_since.return_value = (1000.0, 0)
        self._rule(condition_type="budget_threshold",
                   condition_config={"unit": "usd", "threshold_pct": 50, "period": "day"})
        self._rule(condition_type="budget_threshold",
                   condition_config={"unit": "usd", "threshold_pct": 50, "period": "hour", "usd_limit": 1})
        fired, post = self._fire()
        self.assertEqual(fired, [])
        post.assert_not_called()

    def test_token_rules_are_unchanged(self):
        session = self.Session()
        session.add(RateLimitConfig(scope="agent", scope_id="a1", tokens_per_day=1000, usd_per_day=0.01))
        session.commit()
        session.close()
        self.token_service.get_agent_tokens_since.return_value = 950
        self._rule(condition_type="budget_threshold", condition_config={"threshold_pct": 90, "period": "day"})
        fired, post = self._fire()
        self.assertEqual(post.call_args.args[1]["limit"], 1000)
        self.token_service.get_spend_since.assert_not_called()


class TestApiValidation(_Db):

    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from shared.utils.dashboard.dashboard_server import DashboardServer
        db_client = MagicMock()
        db_client.is_connected.return_value = True
        db_client.get_session.side_effect = lambda: self.Session()
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=db_client):
            server = DashboardServer(app, project_root=Path(__file__).parent.parent.parent)
        server.db_client = db_client
        app.dependency_overrides[server._get_auth_user_dependency] = lambda: "admin"
        as_caller(app, "admin", True)
        self.client = TestClient(app)

    def test_rate_limit_dollar_budgets_must_be_amounts(self):
        svc = MagicMock()
        svc.upsert_config.return_value = {"ok": True}
        with patch("shared.utils.rate_limit_service.get_rate_limit_service", return_value=svc):
            for bad in (-1, "5", True):
                r = self.client.post("/dashboard/api/rate-limits",
                                     json={"scope": "agent", "scope_id": "seller", "usd_per_day": bad})
                self.assertEqual(r.status_code, 400, bad)
            r = self.client.post("/dashboard/api/rate-limits",
                                 json={"scope": "agent", "scope_id": "seller", "usd_per_day": 2.5, "usd_per_month": ""})
        self.assertEqual(r.status_code, 200, r.text)
        kwargs = svc.upsert_config.call_args.kwargs
        self.assertEqual((kwargs["usd_per_day"], kwargs["usd_per_month"]), (2.5, None))

    def test_rate_limit_switches_are_booleans_and_sub_agents_only_for_agents(self):
        svc = MagicMock()
        svc.upsert_config.return_value = {"ok": True}
        with patch("shared.utils.rate_limit_service.get_rate_limit_service", return_value=svc):
            r = self.client.post("/dashboard/api/rate-limits",
                                 json={"scope": "agent", "scope_id": "seller", "block_unpriced": "yes"})
            self.assertEqual(r.status_code, 400)
            r = self.client.post("/dashboard/api/rate-limits", json={
                "scope": "agent", "scope_id": "seller", "block_unpriced": True, "include_sub_agents": True})
            self.assertEqual(r.status_code, 200, r.text)
            kwargs = svc.upsert_config.call_args.kwargs
            self.assertEqual((kwargs["block_unpriced"], kwargs["include_sub_agents"]), (True, True))
            self.client.post("/dashboard/api/rate-limits",
                             json={"scope": "user", "scope_id": "u1", "include_sub_agents": True})
            self.assertFalse(svc.upsert_config.call_args.kwargs["include_sub_agents"])

    def _rule(self, condition_config):
        return self.client.post("/dashboard/api/alert-rules", json={
            "name": "spend", "scope": "agent", "scope_id": "seller", "condition_type": "budget_threshold",
            "condition_config": condition_config, "destination_type": "http",
            "destination_config": {"url": "https://hook.example/x"}})

    def test_dollar_budget_alerts_are_per_day_or_month_with_a_positive_limit(self):
        self.assertEqual(self._rule({"unit": "usd", "period": "hour", "threshold_pct": 90}).status_code, 400)
        self.assertEqual(self._rule({"unit": "usd", "period": "day", "usd_limit": 0}).status_code, 400)
        self.assertEqual(self._rule({"unit": "euro", "period": "day"}).status_code, 400)
        r = self._rule({"unit": "usd", "period": "month", "threshold_pct": 80, "usd_limit": 50})
        self.assertIn(r.status_code, (200, 201), r.text)


if __name__ == "__main__":
    unittest.main()
