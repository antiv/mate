#!/usr/bin/env python3
"""
Tests for pricing model calls in US dollars (shared/utils/model_pricing.py).

A call's cost comes from a manual price, OpenRouter's list (openrouter/*) or
LiteLLM's table. A model none of them prices has no cost, which must never read
as free: LiteLLM reports 0 for models it does not know.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import model_pricing as mp
from shared.utils.models import Base, ModelPrice, TokenUsageLog
from shared.utils.token_usage_service import TokenUsageService

OPENROUTER_PAYLOAD = {"data": [
    {"id": "deepseek/deepseek-chat-v3.1", "pricing": {"prompt": "0.0000002", "completion": "0.0000008"}},
    {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
    {"id": "broken/model", "pricing": {"prompt": "n/a"}},
]}


class _PricingTest(unittest.TestCase):
    """No database and no network unless a test asks for them."""

    def setUp(self):
        mp.forget_manual_prices()
        patches = [
            patch.object(mp, "_manual_prices", return_value={}),
            patch.object(mp, "_openrouter_prices", return_value=mp._parse_openrouter(OPENROUTER_PAYLOAD)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestPriceSources(_PricingTest):

    def test_litellm_prices_a_model_it_knows(self):
        price = mp.price_for("gemini-2.5-flash")
        self.assertEqual(price.source, mp.SOURCE_LITELLM)
        self.assertGreater(price.input_per_token, 0)

    def test_a_model_no_source_knows_has_no_price_not_zero(self):
        self.assertIsNone(mp.price_for("openrouter/unknown/model-x"))
        self.assertIsNone(mp.cost_usd("openrouter/unknown/model-x", 1000, 1000))
        self.assertIsNone(mp.price_for("ollama_chat/some-local-model"))
        self.assertIsNone(mp.price_for(None))

    def test_litellms_zero_for_a_model_it_does_not_know_is_no_price(self):
        with patch("litellm.get_model_info", return_value={"input_cost_per_token": 0, "output_cost_per_token": 0}):
            self.assertIsNone(mp.price_for("openrouter/anthropic/claude-sonnet-4.5"))

    def test_openrouter_models_take_openrouters_price(self):
        price = mp.price_for("openrouter/deepseek/deepseek-chat-v3.1")
        self.assertEqual(price.source, mp.SOURCE_OPENROUTER)
        self.assertAlmostEqual(price.input_per_token, 2e-7)
        self.assertAlmostEqual(mp.cost_usd("openrouter/deepseek/deepseek-chat-v3.1", 1_000_000, 1_000_000), 1.0)

    def test_openrouter_entries_without_a_fixed_price_are_skipped(self):
        prices = mp._parse_openrouter(OPENROUTER_PAYLOAD)
        self.assertEqual(set(prices), {"deepseek/deepseek-chat-v3.1"})

    def test_a_manual_price_wins_and_zero_means_free(self):
        manual = {"gemini-2.5-flash": mp.Price(1e-6, 2e-6, mp.SOURCE_MANUAL),
                  "ollama_chat/gemma4": mp.Price(0.0, 0.0, mp.SOURCE_MANUAL)}
        with patch.object(mp, "_manual_prices", return_value=manual):
            self.assertEqual(mp.price_for("gemini-2.5-flash").source, mp.SOURCE_MANUAL)
            self.assertAlmostEqual(mp.cost_usd("gemini-2.5-flash", 1_000_000, 0), 1.0)
            self.assertEqual(mp.cost_usd("ollama_chat/gemma4", 5000, 5000), 0.0)


class TestBilledTokens(_PricingTest):

    def test_native_gemini_bills_thinking_as_output(self):
        # ADK's Gemini backend reports thinking apart from the response
        self.assertEqual(mp.output_tokens("gemini-2.5-flash", 100, 40), 140)

    def test_through_litellm_the_response_already_includes_reasoning(self):
        for model in ("openai/gpt-4o-mini", "openrouter/google/gemini-2.5-flash", "gemini/gemini-2.5-flash"):
            self.assertEqual(mp.output_tokens(model, 100, 40), 100, model)

    def test_tool_use_prompt_tokens_are_billed_as_input(self):
        price = mp.Price(1e-6, 0.0, mp.SOURCE_MANUAL)
        with patch.object(mp, "_manual_prices", return_value={"m": price}):
            self.assertAlmostEqual(mp.cost_usd("m", 1000, 50, tool_use_tokens=500), 1500e-6)


class _DbTest(_PricingTest):

    def setUp(self):
        super().setUp()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with patch("shared.utils.token_usage_service.get_database_client", return_value=MagicMock()):
            self.service = TokenUsageService()
        self.service._get_session = lambda: self.Session()

    def _row(self, session, model, cost=None, status="SUCCESS"):
        session.add(TokenUsageLog(request_id="r", model_name=model, prompt_tokens=1_000_000,
                                  response_tokens=0, status=status, cost_usd=cost))


class TestLogging(_DbTest):

    def test_a_successful_call_is_priced_when_logged(self):
        row = self.service.log_token_usage("r1", model_name="openrouter/deepseek/deepseek-chat-v3.1",
                                           prompt_tokens=1_000_000, response_tokens=1_000_000)
        self.assertAlmostEqual(row["cost_usd"], 1.0)
        self.assertFalse(row["is_fallback"])

    def test_an_unpriced_model_is_logged_without_a_cost(self):
        row = self.service.log_token_usage("r1", model_name="openrouter/unknown/x",
                                           prompt_tokens=10, response_tokens=10)
        self.assertIsNone(row["cost_usd"])

    def test_failures_and_denials_have_no_cost(self):
        for status in ("ERROR", "ACCESS_DENIED"):
            row = self.service.log_token_usage("r1", model_name="gemini-2.5-flash", prompt_tokens=10,
                                               response_tokens=10, status=status)
            self.assertIsNone(row["cost_usd"], status)

    def test_a_fallback_call_is_marked(self):
        row = self.service.log_token_usage("r1", model_name="gemini-2.5-flash", prompt_tokens=10,
                                           response_tokens=10, is_fallback=True)
        self.assertTrue(row["is_fallback"])

    def test_pricing_trouble_never_loses_the_log_row(self):
        with patch.object(mp, "price_for", side_effect=RuntimeError("boom")):
            row = self.service.log_token_usage("r1", model_name="gemini-2.5-flash", prompt_tokens=10,
                                               response_tokens=10)
        self.assertIsNotNone(row)
        self.assertIsNone(row["cost_usd"])


class TestRecompute(_DbTest):

    def test_missing_costs_are_filled_in_and_priced_ones_kept(self):
        session = self.Session()
        self._row(session, "openrouter/deepseek/deepseek-chat-v3.1")
        self._row(session, "openrouter/deepseek/deepseek-chat-v3.1", cost=9.0)
        self._row(session, "openrouter/unknown/x")
        self._row(session, "openrouter/deepseek/deepseek-chat-v3.1", status="ERROR")
        session.commit()
        self.assertEqual(mp.recompute_costs(session), 1)
        costs = sorted((r.model_name, r.status, r.cost_usd) for r in self.Session().query(TokenUsageLog).all())
        self.assertIn(("openrouter/deepseek/deepseek-chat-v3.1", "SUCCESS", 9.0), costs)
        self.assertIn(("openrouter/deepseek/deepseek-chat-v3.1", "ERROR", None), costs)
        self.assertIn(("openrouter/unknown/x", "SUCCESS", None), costs)
        self.assertEqual(sum(1 for c in costs if c[2] is not None and abs(c[2] - 0.2) < 1e-9), 1)

    def test_a_models_calls_can_all_be_priced_again(self):
        session = self.Session()
        self._row(session, "openrouter/deepseek/deepseek-chat-v3.1", cost=9.0)
        session.commit()
        mp.recompute_costs(session, model_name="openrouter/deepseek/deepseek-chat-v3.1", only_missing=False)
        self.assertAlmostEqual(self.Session().query(TokenUsageLog).one().cost_usd, 0.2)


class TestPriceRows(_DbTest):

    def test_models_used_or_priced_by_hand_are_listed(self):
        session = self.Session()
        self._row(session, "openrouter/deepseek/deepseek-chat-v3.1", cost=0.2)
        self._row(session, "openrouter/unknown/x")
        session.add(ModelPrice(model_name="my/local", input_usd_per_mtok=0, output_usd_per_mtok=0))
        session.commit()
        manual = {"my/local": mp.Price(0.0, 0.0, mp.SOURCE_MANUAL)}
        with patch.object(mp, "_manual_prices", return_value=manual):
            rows = {r["model_name"]: r for r in mp.model_price_rows(self.Session())}
        self.assertEqual(rows["openrouter/deepseek/deepseek-chat-v3.1"]["source"], mp.SOURCE_OPENROUTER)
        self.assertAlmostEqual(rows["openrouter/deepseek/deepseek-chat-v3.1"]["input_usd_per_mtok"], 0.2)
        self.assertIsNone(rows["openrouter/unknown/x"]["source"])
        self.assertEqual(rows["openrouter/unknown/x"]["unpriced_calls"], 1)
        self.assertEqual(rows["my/local"]["source"], mp.SOURCE_MANUAL)
        self.assertEqual(rows["my/local"]["manual"], {"input_usd_per_mtok": 0, "output_usd_per_mtok": 0})


class _DashboardTest(_PricingTest):
    """The Usage page's cost panel and the Settings page's price routes, on SQLite."""

    def setUp(self):
        super().setUp()
        from pathlib import Path
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from shared.utils.dashboard.dashboard_server import DashboardServer
        from shared.utils.models import AgentConfig, Project

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        db_client = MagicMock()
        db_client.is_connected.return_value = True
        db_client.get_session.side_effect = lambda: self.Session()

        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=db_client):
            self.server = DashboardServer(app, project_root=Path(__file__).parent.parent.parent)
        self.server.db_client = db_client
        self.server._get_is_admin = lambda request: True
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "admin"
        self.client = TestClient(app)

        session = self.Session()
        session.add(Project(id=1, name="Shop"))
        session.add(Project(id=2, name="Support"))
        session.add(AgentConfig(name="seller", type="llm", project_id=1))
        session.add(AgentConfig(name="helper", type="llm", project_id=2))
        session.commit()
        session.close()

    def _log(self, agent, model, cost, fallback=False, status="SUCCESS", tokens=1000):
        session = self.Session()
        session.add(TokenUsageLog(request_id="r", agent_name=agent, model_name=model, prompt_tokens=tokens,
                                  response_tokens=0, status=status, cost_usd=cost, is_fallback=fallback))
        session.commit()
        session.close()


class TestCostStats(_DashboardTest):

    def _stats(self):
        from datetime import datetime, timedelta
        session = self.Session()
        try:
            now = datetime.utcnow()
            return self.server._get_cost_stats(session, now - timedelta(days=1), now + timedelta(minutes=1))
        finally:
            session.close()

    def test_cost_by_agent_and_project_with_the_fallback_apart(self):
        self._log("seller", "a", 1.0)
        self._log("seller", "b", 0.5, fallback=True)
        self._log("helper", "a", 0.25)
        self._log("helper", "x", None)
        self._log("seller", "a", 7.0, status="ERROR")
        self._log("deleted_agent", "a", 0.1)  # an agent no longer configured has no project
        stats = self._stats()
        self.assertAlmostEqual(stats["total_usd"], 1.85)
        self.assertAlmostEqual(stats["fallback_usd"], 0.5)
        self.assertEqual(stats["fallback_calls"], 1)
        self.assertEqual(stats["unpriced_calls"], 1)
        self.assertEqual(stats["unpriced_tokens_pct"], 20.0)
        agents = {r["agent"]: r for r in stats["by_agent"]}
        self.assertAlmostEqual(agents["seller"]["cost_usd"], 1.5)
        self.assertAlmostEqual(agents["seller"]["fallback_cost_usd"], 0.5)
        self.assertEqual(agents["helper"]["unpriced_calls"], 1)
        self.assertEqual([r["project"] for r in stats["by_project"]], ["Shop", "Support", "No project"])
        self.assertAlmostEqual(stats["by_project"][0]["cost_usd"], 1.5)

    def test_no_calls_cost_nothing(self):
        stats = self._stats()
        self.assertEqual((stats["total_usd"], stats["unpriced_tokens_pct"], stats["by_agent"]), (0.0, 0.0, []))


class TestPriceRoutes(_DashboardTest):
    URL = "/dashboard/api/settings/model-prices"

    def _manual(self):
        from shared.utils.models import ModelPrice
        session = self.Session()
        try:
            return {r.model_name: (r.input_usd_per_mtok, r.output_usd_per_mtok, r.updated_by)
                    for r in session.query(ModelPrice).all()}
        finally:
            session.close()

    def _costs(self):
        session = self.Session()
        try:
            return [r.cost_usd for r in session.query(TokenUsageLog).order_by(TokenUsageLog.id).all()]
        finally:
            session.close()

    def test_a_price_set_by_hand_reprices_all_the_models_calls(self):
        self._log("seller", "my/local", None, tokens=1_000_000)
        self._log("seller", "my/local", 9.0, tokens=1_000_000)
        # The routes read manual prices from the database, not the test's empty stub
        with patch.object(mp, "_manual_prices", side_effect=lambda: {
                n: mp.Price(i / 1e6, o / 1e6, mp.SOURCE_MANUAL) for n, (i, o, _) in self._manual().items()}):
            r = self.client.put(self.URL, json={"model_name": "my/local", "input_usd_per_mtok": 2,
                                                "output_usd_per_mtok": 3})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self._manual(), {"my/local": (2.0, 3.0, "admin")})
            self.assertEqual(self._costs(), [2.0, 2.0])
            row = next(m for m in r.json()["models"] if m["model_name"] == "my/local")
            self.assertEqual(row["source"], mp.SOURCE_MANUAL)

            r = self.client.delete(self.URL, params={"model": "my/local"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self._manual(), {})
            self.assertEqual(self._costs(), [None, None])

    def test_bad_prices_are_refused(self):
        for body in ({"model_name": "m", "input_usd_per_mtok": -1, "output_usd_per_mtok": 1},
                     {"model_name": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": 1},
                     {"model_name": "m", "input_usd_per_mtok": True, "output_usd_per_mtok": 1},
                     {"model_name": "two words", "input_usd_per_mtok": 1, "output_usd_per_mtok": 1},
                     {"model_name": "", "input_usd_per_mtok": 1, "output_usd_per_mtok": 1}):
            self.assertEqual(self.client.put(self.URL, json=body).status_code, 400, body)
        self.assertEqual(self._manual(), {})

    def test_removing_a_price_that_is_not_there_is_404(self):
        self.assertEqual(self.client.delete(self.URL, params={"model": "nope"}).status_code, 404)

    def test_fill_missing_prices_calls_logged_without_a_cost(self):
        self._log("seller", "openrouter/deepseek/deepseek-chat-v3.1", None, tokens=1_000_000)
        r = self.client.post(self.URL + "/fill-missing")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["priced"], 1)
        self.assertAlmostEqual(self._costs()[0], 0.2)

    def test_only_admins(self):
        self.server._get_is_admin = lambda request: False
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self.client.post(self.URL + "/fill-missing").status_code, 403)


if __name__ == "__main__":
    unittest.main()
