#!/usr/bin/env python3
"""
Tests for the Playground (shared/utils/eval_playground.py and its endpoints).

A variant is the agent's config with another model, other instructions, or a
stored version. Each runs the same prompts in memory; replies are timed, scored
when there is an expected output, and priced from the token rows their run
wrote, which both runtimes key by invocation id.
"""

import asyncio
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.utils.eval_playground import Case, Variant, add_costs, run_variant, summarize
from shared.utils.eval_runner import EvalRunner
from shared.utils.models import AgentConfig, AgentConfigVersion, Base, TestCase, TokenUsageLog

PROJECT_ROOT = Path(__file__).parent.parent.parent


class FakeAgent:
    """Answers with its model and instruction, and logs a priced token row as the callbacks do."""

    session_factory = None
    built = []

    def __init__(self, snapshot, manager=None):
        self.snapshot = snapshot

    async def __aenter__(self):
        if self.snapshot.get("model_name") == "broken/model":
            raise RuntimeError("Unknown model broken/model")
        FakeAgent.built.append(dict(self.snapshot))
        return self

    async def __aexit__(self, *exc):
        return None

    async def ask(self, input_text, timeout=120.0, events=None):
        if input_text == "fail":
            raise TimeoutError("no answer")
        invocation = f"e-{uuid.uuid4()}"
        if events is not None:
            events.append({"invocation_id": invocation, "author": self.snapshot["name"]})
        if FakeAgent.session_factory:
            session = FakeAgent.session_factory()
            cost = None if self.snapshot.get("model_name") == "unpriced/model" else 0.01
            session.add(TokenUsageLog(request_id=invocation, agent_name=self.snapshot["name"],
                                      model_name=self.snapshot.get("model_name"), prompt_tokens=100,
                                      response_tokens=20, status="SUCCESS", cost_usd=cost))
            session.commit()
            session.close()
        return f"{self.snapshot.get('model_name')}: {self.snapshot.get('instruction')}"


class TestRunVariant(unittest.TestCase):

    def test_replies_are_timed_and_scored_when_an_answer_is_expected(self):
        variant = Variant("A", {"name": "bot", "model_name": "m1", "instruction": "hi"})
        cases = [Case(input="q1", expected_output="m1: hi"), Case(input="q2")]
        asyncio.run(run_variant(variant, cases, EvalRunner(), agent_factory=FakeAgent))
        first, second = variant.results
        self.assertEqual((first["output"], first["score"], first["passed"]), ("m1: hi", 1.0, True))
        self.assertIsNone(second["score"], "no expected output, no score")
        self.assertGreaterEqual(first["latency_ms"], 0)
        self.assertEqual(len(first["invocation_ids"]), 1)

    def test_a_failed_reply_is_kept_and_the_rest_still_run(self):
        variant = Variant("A", {"name": "bot", "model_name": "m1", "instruction": "x"})
        asyncio.run(run_variant(variant, [Case(input="fail"), Case(input="ok")], EvalRunner(),
                                agent_factory=FakeAgent))
        self.assertIn("TimeoutError", variant.results[0]["error"])
        self.assertIsNone(variant.results[0]["output"])
        self.assertEqual(variant.results[1]["output"], "m1: x")

    def test_an_agent_that_cannot_be_built_is_an_error_of_its_variant(self):
        variant = Variant("A", {"name": "bot", "model_name": "broken/model"})
        asyncio.run(run_variant(variant, [Case(input="q")], EvalRunner(), agent_factory=FakeAgent))
        self.assertIn("Unknown model", variant.error)
        self.assertEqual(variant.results, [])

    def test_summary(self):
        variant = Variant("A", {})
        variant.results = [
            {"output": "x", "score": 1.0, "passed": True, "latency_ms": 1000, "cost_usd": 0.02, "tokens": 10, "unpriced_calls": 0},
            {"output": "y", "score": 0.5, "passed": False, "latency_ms": 3000, "cost_usd": None, "tokens": 5, "unpriced_calls": 1},
            {"output": None, "score": None, "passed": None, "latency_ms": 9000, "cost_usd": None, "tokens": 0, "unpriced_calls": 0},
        ]
        s = summarize(variant)
        self.assertEqual((s["passed"], s["scored"], s["errors"], s["avg_score"]), (1, 2, 1, 0.75))
        self.assertEqual(s["avg_latency_ms"], 2000, "failed replies are not in the latency")
        self.assertEqual((s["cost_usd"], s["tokens"], s["unpriced_calls"]), (0.02, 15, 1))


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)


class TestCosts(_Db):

    def test_each_reply_is_priced_from_its_runs_rows(self):
        session = self.Session()
        for rid, cost in (("r1", 0.01), ("r1", 0.02), ("r2", None), ("other", 5.0)):
            session.add(TokenUsageLog(request_id=rid, prompt_tokens=10, response_tokens=5, status="SUCCESS", cost_usd=cost))
        session.add(TokenUsageLog(request_id="r1", prompt_tokens=10, response_tokens=0, status="ERROR", cost_usd=None))
        session.commit()
        variant = Variant("A", {})
        variant.results = [{"invocation_ids": ["r1"]}, {"invocation_ids": ["r2"]}, {"invocation_ids": []}]
        add_costs(session, [variant])
        r1, r2, none = variant.results
        self.assertAlmostEqual(r1["cost_usd"], 0.03)
        self.assertEqual((r1["tokens"], r1["unpriced_calls"]), (30, 0))
        self.assertEqual((r2["cost_usd"], r2["unpriced_calls"]), (None, 1))
        self.assertEqual((none["cost_usd"], none["tokens"]), (None, 0))
        session.close()


class TestEndpoints(_Db):

    def setUp(self):
        super().setUp()
        db_client = MagicMock()
        db_client.is_connected.return_value = True
        db_client.get_session.side_effect = lambda: self.Session()
        from shared.utils.dashboard.dashboard_server import DashboardServer
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=db_client):
            server = DashboardServer(app, project_root=PROJECT_ROOT)
        server.db_client = db_client
        app.dependency_overrides[server._get_auth_user_dependency] = lambda: "admin"
        self.client = TestClient(app)

        session = self.Session()
        bot = AgentConfig(name="bot", type="llm", model_name="m-current", instruction="CURRENT", project_id=1)
        other = AgentConfig(name="other", type="llm", model_name="m", instruction="O", project_id=1)
        session.add_all([bot, other])
        session.flush()
        v1 = AgentConfigVersion(agent_config_id=bot.id, version_number=1,
                                config_snapshot=json.dumps({"name": "bot", "type": "llm",
                                                            "model_name": "m-old", "instruction": "OLD"}))
        vo = AgentConfigVersion(agent_config_id=other.id, version_number=1,
                                config_snapshot=json.dumps({"name": "other", "instruction": "O"}))
        session.add_all([v1, vo, TestCase(agent_name="bot", input="q", expected_output="m-x: CURRENT",
                                          eval_method="exact_match")])
        session.commit()
        self.v1, self.vo = v1.id, vo.id
        session.close()

        FakeAgent.built = []
        FakeAgent.session_factory = self.Session
        self.addCleanup(setattr, FakeAgent, "session_factory", None)
        p = patch("shared.utils.eval_agent_runner.SnapshotAgent", FakeAgent)
        p.start()
        self.addCleanup(p.stop)

    def _run(self, **body):
        return self.client.post("/dashboard/api/evals/playground/run", json=dict({"agent_name": "bot"}, **body))

    def test_agent_details_to_start_variants_from(self):
        data = self.client.get("/dashboard/api/evals/playground/agent/bot").json()
        self.assertEqual((data["model_name"], data["instruction"], data["active_cases"]), ("m-current", "CURRENT", 1))
        self.assertEqual([v["version_number"] for v in data["versions"]], [1])
        agents = self.client.get("/dashboard/api/evals/playground/agents").json()["agents"]
        self.assertEqual({a["name"]: a["active_cases"] for a in agents}, {"bot": 1, "other": 0})

    def test_a_prompt_on_variants_side_by_side(self):
        r = self._run(mode="prompt", prompt="hello", expected_output="m-x: CURRENT", eval_method="exact_match",
                      variants=[{"model_name": "m-x"},
                                {"label": "Old", "version_id": self.v1},
                                {"model_name": "unpriced/model", "instruction": "NEW"}])
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual([v["label"] for v in data["variants"]], ["Variant A", "Old", "Variant C"])
        self.assertEqual([b["model_name"] for b in FakeAgent.built], ["m-x", "m-old", "unpriced/model"])
        self.assertEqual([b["instruction"] for b in FakeAgent.built], ["CURRENT", "OLD", "NEW"])
        self.assertEqual(FakeAgent.built[1]["name"], "bot")
        results = data["cases"][0]["results"]
        self.assertEqual([r["output"] for r in results], ["m-x: CURRENT", "m-old: OLD", "unpriced/model: NEW"])
        self.assertEqual([r["passed"] for r in results], [True, False, False])
        self.assertEqual([r["cost_usd"] for r in results], [0.01, 0.01, None])
        self.assertEqual(data["variants"][2]["summary"]["unpriced_calls"], 1)
        self.assertNotIn("invocation_ids", results[0])

    def test_the_eval_suite_on_variants(self):
        r = self._run(mode="suite", variants=[{"model_name": "m-x"}, {}])
        self.assertEqual(r.status_code, 200, r.text)
        summaries = [v["summary"] for v in r.json()["variants"]]
        self.assertEqual([(s["passed"], s["scored"]) for s in summaries], [(1, 1), (0, 1)])

    def test_a_variant_that_cannot_be_built_does_not_stop_the_others(self):
        r = self._run(mode="prompt", prompt="hi", variants=[{"model_name": "broken/model"}, {}])
        self.assertEqual(r.status_code, 200, r.text)
        variants = r.json()["variants"]
        self.assertIn("Unknown model", variants[0]["error"])
        self.assertIsNone(variants[1]["error"])

    def test_bad_requests_are_refused(self):
        bad = [
            dict(mode="chat", prompt="x", variants=[{}, {}]),
            dict(mode="prompt", prompt="x", variants=[{}]),
            dict(mode="prompt", prompt="x", variants=[{}, {}, {}, {}]),
            dict(mode="prompt", prompt="", variants=[{}, {}]),
            dict(mode="prompt", prompt="x", eval_method="vibes", variants=[{}, {}]),
            dict(mode="prompt", prompt="x", variants=[{"version_id": self.vo}, {}]),
            dict(mode="prompt", prompt="x", variants=[{"model_name": "two words"}, {}]),
        ]
        for body in bad:
            self.assertEqual(self._run(**body).status_code, 400, body)
        self.assertEqual(self.client.post("/dashboard/api/evals/playground/run",
                                          json={"agent_name": "nope", "mode": "prompt", "prompt": "x",
                                                "variants": [{}, {}]}).status_code, 404)
        self.assertEqual(FakeAgent.built, [])

    def test_suite_mode_needs_test_cases(self):
        r = self.client.post("/dashboard/api/evals/playground/run",
                             json={"agent_name": "other", "mode": "suite", "variants": [{}, {}]})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
