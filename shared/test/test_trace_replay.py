#!/usr/bin/env python3
"""
Tests for the session replay built from trace_spans.

Spans are written by the real DatabaseSpanExporter into an in-memory SQLite
trace_spans table, shaped like the ones ADK and MATE's callbacks record, then
read back through the dashboard's replay methods.
"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import HTTPException
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.utils.dashboard.dashboard_server import DashboardServer
from shared.utils.tracing.db_span_exporter import DatabaseSpanExporter
from shared.utils.tracing.replay import TURN_GAP_MS, build_turn

ROOT = Path(__file__).resolve().parent.parent.parent


class _Db:
    def __init__(self, engine):
        self._sessions = sessionmaker(bind=engine)

    def is_connected(self):
        return True

    def get_session(self):
        return self._sessions()


def _record_turn(tracer, session_id, fallback=True):
    """One turn: root agent calls a model (answered by the fallback), then a sub-agent uses a tool."""
    with tracer.start_as_current_span("invoke_agent root") as root:
        root.set_attributes({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "root",
                             "gen_ai.conversation.id": session_id})
        with tracer.start_as_current_span("call_llm") as call:
            call.set_attribute("gen_ai.request.model", "gemini-2.5-flash")
            with tracer.start_as_current_span("gen_ai.inference") as inference:
                inference.set_attributes({"mate.agent.name": "root", "gen_ai.conversation.id": session_id,
                                          "gen_ai.request.model": "gemini-2.5-flash"})
                if fallback:
                    inference.set_attributes({"gen_ai.response.model": "openai/gpt-4o-mini",
                                              "mate.model.fallback": True})
        with tracer.start_as_current_span("invoke_agent researcher") as sub:
            sub.set_attributes({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "researcher",
                                "gen_ai.conversation.id": session_id})
            with tracer.start_as_current_span("execute_tool search") as tool:
                tool.set_attributes({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search",
                                     "gen_ai.agent.name": "researcher"})
                with tracer.start_as_current_span("mate.tool") as mate_tool:
                    mate_tool.set_attributes({"mate.tool.name": "search", "mate.agent.name": "researcher"})


def _record_sub_agent_turn(tracer, session_id):
    """A later turn that ADK routes straight to the sub-agent the root handed off to."""
    with tracer.start_as_current_span("invoke_agent researcher") as sub:
        sub.set_attributes({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "researcher",
                            "gen_ai.conversation.id": session_id})
        with tracer.start_as_current_span("execute_tool search") as tool:
            tool.set_attributes({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search",
                                 "gen_ai.agent.name": "researcher"})


def _record_other_project_turn(tracer, session_id):
    with tracer.start_as_current_span("invoke_agent other") as root:
        root.set_attributes({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "other",
                             "gen_ai.conversation.id": session_id})


class TestSessionReplay(unittest.TestCase):

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        sql = (ROOT / "shared/sql/migrations/sqlite/V007__trace_spans.sql").read_text()
        with engine.begin() as conn:
            for statement in sql.split(";"):
                body = "\n".join(l for l in statement.splitlines() if not l.strip().startswith("--")).strip()
                if body:
                    conn.exec_driver_sql(body)
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE agents_config (name VARCHAR(255), project_id INTEGER)")
            conn.exec_driver_sql("INSERT INTO agents_config VALUES ('root', 1), ('researcher', 1), ('other', 2)")
        db = _Db(engine)
        exporter = DatabaseSpanExporter()
        exporter._db_client = db
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.tracer = provider.get_tracer("test")
        self.server = DashboardServer.__new__(DashboardServer)
        self.server.db_client = db

    def test_replay_shows_agents_fallback_and_tools_once(self):
        _record_turn(self.tracer, "sess-1")
        _record_turn(self.tracer, "sess-2", fallback=False)

        replay = self.server._get_session_replay("sess-1")
        self.assertEqual(len(replay["turns"]), 1)
        turn = replay["turns"][0]
        self.assertEqual(set(turn["agents"]), {"root", "researcher"})

        root, researcher = turn["agents"]["root"], turn["agents"]["researcher"]
        # ADK's call_llm and MATE's gen_ai.inference describe the same call: counted once
        self.assertEqual(root["llm_calls"], 1)
        self.assertTrue(root["fallback"])
        self.assertEqual(root["models"], ["openai/gpt-4o-mini"])
        # ADK's execute_tool and MATE's mate.tool describe the same call: counted once
        self.assertEqual(researcher["tool_calls"], 1)
        self.assertFalse(researcher["fallback"])

        llm = [e for e in turn["events"] if e["kind"] == "llm"]
        self.assertEqual(llm[0]["primary_model"], "gemini-2.5-flash")
        self.assertEqual(llm[0]["model"], "openai/gpt-4o-mini")
        starts = [e["start_ms"] for e in turn["events"]]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(0 <= e["start_ms"] <= e["end_ms"] <= turn["duration_ms"] for e in turn["events"]))
        # The root agent ran first
        self.assertEqual(turn["events"][0]["kind"], "agent")
        self.assertEqual(turn["events"][0]["agent"], "root")

    def test_each_turn_is_a_trace(self):
        _record_turn(self.tracer, "sess-1")
        _record_turn(self.tracer, "sess-1", fallback=False)
        turns = self.server._get_session_replay("sess-1")["turns"]
        self.assertEqual(len(turns), 2)
        self.assertLessEqual(turns[0]["started_at"], turns[1]["started_at"])
        self.assertFalse(turns[1]["agents"]["root"]["fallback"])

    def test_session_list(self):
        _record_turn(self.tracer, "sess-1")
        _record_turn(self.tracer, "sess-1")
        _record_turn(self.tracer, "sess-2")
        sessions = {s["session_id"]: s for s in self.server._list_replay_sessions()["sessions"]}
        self.assertEqual(set(sessions), {"sess-1", "sess-2"})
        self.assertEqual(sessions["sess-1"]["turns"], 2)
        self.assertEqual(sessions["sess-1"]["agents"], ["researcher", "root"])

    def test_session_plays_as_one_timeline(self):
        # Turn 1 on the root agent, turn 2 handed off to the sub-agent: one clock for both
        _record_turn(self.tracer, "sess-1")
        _record_sub_agent_turn(self.tracer, "sess-1")
        replay = self.server._get_session_replay("sess-1")
        turns, timeline = replay["turns"], replay["timeline"]
        self.assertEqual(len(turns), 2)
        self.assertEqual([m["index"] for m in timeline["turns"]], [0, 1])
        second = timeline["turns"][1]
        # The person's think time between the turns is replaced by a short fixed gap
        self.assertEqual(second["start_ms"], turns[0]["duration_ms"] + TURN_GAP_MS)
        self.assertEqual(timeline["duration_ms"], second["end_ms"])
        self.assertEqual({e["turn"] for e in timeline["events"]}, {0, 1})
        starts = [e["start_ms"] for e in timeline["events"]]
        self.assertEqual(starts, sorted(starts))
        later = [e for e in timeline["events"] if e["turn"] == 1]
        self.assertTrue(all(second["start_ms"] <= e["start_ms"] <= e["end_ms"] <= second["end_ms"] for e in later))
        self.assertEqual({e["agent"] for e in later}, {"researcher"})
        # Counts cover the whole session
        self.assertEqual(timeline["agents"]["researcher"]["tool_calls"], 2)

    def test_session_list_by_project(self):
        _record_turn(self.tracer, "sess-1")
        _record_other_project_turn(self.tracer, "sess-2")
        mine = self.server._list_replay_sessions(project_id=1)["sessions"]
        theirs = self.server._list_replay_sessions(project_id=2)["sessions"]
        self.assertEqual([s["session_id"] for s in mine], ["sess-1"])
        self.assertEqual([s["session_id"] for s in theirs], ["sess-2"])
        self.assertEqual(len(self.server._list_replay_sessions()["sessions"]), 2)

    def test_unknown_session_has_no_turns(self):
        _record_turn(self.tracer, "sess-1")
        self.assertEqual(self.server._get_session_replay("sess-10")["turns"], [])

    def test_invalid_session_id_is_refused(self):
        for bad in ("", "a%b", "x" * 200, "a b"):
            with self.assertRaises(HTTPException) as cm:
                self.server._get_session_replay(bad)
            self.assertEqual(cm.exception.status_code, 400)

    def test_attributes_cast_to_text_per_dialect(self):
        # JSONB (PostgreSQL) and JSON (MySQL) columns take no LIKE without a cast
        for dialect, expected in (("postgresql", "CAST(attributes AS TEXT)"),
                                  ("mysql", "CAST(attributes AS CHAR)"), ("sqlite", "attributes")):
            session = MagicMock()
            session.get_bind.return_value.dialect.name = dialect
            session.execute.return_value.fetchall.return_value = []
            self.server._replay_spans(session, "{attributes_text} LIKE :pat", {"pat": "%x%"})
            self.assertIn(f"WHERE {expected} LIKE :pat", str(session.execute.call_args[0][0]))

    def test_attributes_already_parsed_by_the_driver(self):
        # psycopg2 returns JSONB as a dict, not text
        session = MagicMock()
        session.get_bind.return_value.dialect.name = "postgresql"
        session.execute.return_value.fetchall.return_value = [
            ("t", "s", None, "invoke_agent root", "2026-10-15T10:00:00+00:00", None,
             {"gen_ai.conversation.id": "sess-1"}, "OK", None)]
        spans = self.server._replay_spans(session, "trace_id IN :tids", {"tids": ["t"]})
        self.assertEqual(spans[0]["attributes"], {"gen_ai.conversation.id": "sess-1"})

    def test_turn_without_agent_spans_is_skipped(self):
        spans = [{"span_id": "1", "parent_span_id": None, "name": "mate.rbac_check",
                  "start_time": "2026-10-15T10:00:00+00:00", "end_time": "2026-10-15T10:00:01+00:00",
                  "attributes": {}, "status": "OK", "error_message": None}]
        self.assertIsNone(build_turn("t", spans))



class TestFallbackOnInferenceSpan(unittest.TestCase):
    """The token usage callbacks mark the gen_ai.inference span a fallback answered."""

    def _run_call(self, fallback):
        from shared.callbacks import token_usage_callback as tuc
        from shared.utils.tracing import tracing_config
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        ctx = SimpleNamespace(state={}, agent_name="root")
        request = SimpleNamespace(model="gemini-2.5-flash")
        response = LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5, total_token_count=15))
        service = MagicMock()
        service.is_connected.return_value = False
        with patch.object(tracing_config, "_TRACING_ENABLED", True), \
                patch("shared.utils.tracing.tracer.get_tracer", side_effect=lambda *a, **k: provider.get_tracer("t")), \
                patch.object(tuc, "get_token_usage_service", return_value=service), \
                patch.object(tuc, "_get_adk_session_info", return_value=("u1", "sess-1")):
            tuc.capture_model_name_callback(ctx, request)
            if fallback:  # what make_model_fallback_callback sets when the fallback answers
                ctx.state["current_model_name"] = "openai/gpt-4o-mini"
                ctx.state["current_model_is_fallback"] = True
            tuc.log_token_usage_callback(ctx, response)
        spans = [s for s in exporter.get_finished_spans() if s.name == "gen_ai.inference"]
        self.assertEqual(len(spans), 1)
        return dict(spans[0].attributes)

    def test_fallback_answer_is_marked(self):
        attrs = self._run_call(fallback=True)
        self.assertIs(attrs.get("mate.model.fallback"), True)
        self.assertEqual(attrs["gen_ai.request.model"], "gemini-2.5-flash")
        self.assertEqual(attrs["gen_ai.response.model"], "openai/gpt-4o-mini")
        self.assertEqual(attrs["gen_ai.conversation.id"], "sess-1")

    def test_primary_answer_is_not_marked(self):
        attrs = self._run_call(fallback=False)
        self.assertNotIn("mate.model.fallback", attrs)


if __name__ == "__main__":
    unittest.main()
