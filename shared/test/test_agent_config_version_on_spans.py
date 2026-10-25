#!/usr/bin/env python3
"""
Which agent config version answered, on spans and in Trace Replay (#274).

When an agent is built, MATE records the hash of the config it was built from
and, when a stored version holds that config, the version's id and number. The
gen_ai.inference span of each model call carries them, and the replay shows them
per agent, so two replays of one agent that look alike can be told apart when
they ran on different configs.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.adk.models.llm_response import LlmResponse
from google.genai import types
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import agent_config_identity as identity_module
from shared.utils.agent_config_identity import config_hash, config_snapshot, identify, loaded, record_loaded
from shared.utils.models import AgentConfig, AgentConfigVersion, Base
from shared.utils.tracing.replay import build_timeline, build_turn


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.db = MagicMock()
        self.db.get_session.side_effect = lambda: self.Session()
        session = self.Session()
        agent = AgentConfig(name="support", type="llm", model_name="gemini-2.5-flash",
                            instruction="v1", project_id=1)
        session.add(agent)
        session.commit()
        self.agent_id = agent.id
        session.close()

    def _save_version(self, number, snapshot=None):
        """What a dashboard save does: a version holding the row as it is now."""
        session = self.Session()
        row = session.get(AgentConfig, self.agent_id)
        version = AgentConfigVersion(agent_config_id=self.agent_id, version_number=number)
        version.set_snapshot(snapshot if snapshot is not None else config_snapshot(row))
        session.add(version)
        session.commit()
        version_id = version.id
        session.close()
        return version_id

    def _change(self, **fields):
        session = self.Session()
        row = session.get(AgentConfig, self.agent_id)
        for key, value in fields.items():
            setattr(row, key, value)
        session.commit()
        session.close()

    def _identify(self):
        session = self.Session()
        try:
            return identify(session, session.get(AgentConfig, self.agent_id))
        finally:
            session.close()


class TestIdentify(_Db):

    def test_a_config_a_version_holds_is_that_version(self):
        self._save_version(1)
        self._change(instruction="v2")
        v2 = self._save_version(2)
        identity = self._identify()
        self.assertEqual((identity["version_id"], identity["version"]), (v2, 2))

    def test_a_rolled_back_config_matches_the_older_version_it_equals(self):
        v1 = self._save_version(1)
        self._change(instruction="v2")
        self._save_version(2)
        self._change(instruction="v1")
        self.assertEqual(self._identify()["version_id"], v1)

    def test_a_config_no_version_holds_has_only_its_hash(self):
        self._save_version(1)
        self._change(instruction="edited by the create_agent tool")
        identity = self._identify()
        self.assertIsNone(identity["version_id"])
        self.assertIsNone(identity["version"])
        self.assertEqual(len(identity["hash"]), 12)

    def test_different_configs_have_different_hashes(self):
        first = self._identify()["hash"]
        self._change(instruction="v2")
        self.assertNotEqual(self._identify()["hash"], first)

    def test_a_snapshot_from_before_a_field_was_added_still_matches(self):
        session = self.Session()
        snapshot = config_snapshot(session.get(AgentConfig, self.agent_id))
        session.close()
        snapshot.pop("fallback_model")
        snapshot.pop("ai_disclosure_waiver")
        v1 = self._save_version(1, snapshot)
        self.assertEqual(self._identify()["version_id"], v1)

    def test_the_api_key_is_not_in_the_hash(self):
        snapshot = {"name": "a", "instruction": "x", "model_api_key": "sk-1"}
        self.assertEqual(config_hash(snapshot), config_hash(dict(snapshot, model_api_key="sk-2")))


class TestRecordedWhenBuilt(_Db):

    def test_the_adk_build_records_the_version_it_built(self):
        v1 = self._save_version(1)
        with patch("shared.utils.agent_manager.get_database_client", return_value=self.db):
            from shared.utils.agent_manager import AgentManager
            manager = AgentManager()
        manager.db_client = self.db
        session = self.Session()
        row = session.get(AgentConfig, self.agent_id)
        session.expunge(row)
        session.close()
        with patch("shared.utils.file_search_service.FileSearchService") as fs:
            fs.return_value.get_stores_for_agent.return_value = []
            manager.initialize_agent_from_config(row)
        self.assertEqual(loaded("support")["version_id"], v1)

    def test_what_was_loaded_stays_until_the_agent_is_rebuilt(self):
        record_loaded("support", {"hash": "aaa", "version_id": 7, "version": 3})
        self._change(instruction="changed in the database")
        self.assertEqual(loaded("support")["version"], 3)

    def test_an_error_is_logged_and_swallowed(self):
        db = MagicMock()
        db.get_session.return_value.query.side_effect = RuntimeError("db down")
        with self.assertLogs("shared.utils.agent_config_identity", "WARNING"):
            identity_module.record_for_row(db, SimpleNamespace(name="x"), "x")


class TestOnTheInferenceSpan(unittest.TestCase):

    def _attrs(self, agent_name):
        from shared.callbacks import token_usage_callback as tuc
        from shared.utils.tracing import tracing_config
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        ctx = SimpleNamespace(state={}, agent_name=agent_name)
        response = LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1, candidates_token_count=1, total_token_count=2))
        service = MagicMock()
        service.is_connected.return_value = False
        with patch.object(tracing_config, "_TRACING_ENABLED", True), \
                patch("shared.utils.tracing.tracer.get_tracer", side_effect=lambda *a, **k: provider.get_tracer("t")), \
                patch.object(tuc, "get_token_usage_service", return_value=service), \
                patch.object(tuc, "_get_adk_session_info", return_value=("u1", "s1")):
            tuc.capture_model_name_callback(ctx, SimpleNamespace(model="gemini-2.5-flash"))
            tuc.log_token_usage_callback(ctx, response)
        return dict(next(s for s in exporter.get_finished_spans() if s.name == "gen_ai.inference").attributes)

    def test_a_versioned_config(self):
        record_loaded("span_agent", {"hash": "abc123def456", "version_id": 41, "version": 12})
        attrs = self._attrs("span_agent")
        self.assertEqual(attrs["mate.agent.config_version_id"], 41)
        self.assertEqual(attrs["mate.agent.config_version"], 12)
        self.assertEqual(attrs["mate.agent.config_hash"], "abc123def456")

    def test_an_unversioned_config_has_only_its_hash(self):
        record_loaded("loose_agent", {"hash": "fff000fff000", "version_id": None, "version": None})
        attrs = self._attrs("loose_agent")
        self.assertEqual(attrs["mate.agent.config_hash"], "fff000fff000")
        self.assertNotIn("mate.agent.config_version", attrs)

    def test_an_agent_never_recorded_gets_nothing(self):
        attrs = self._attrs("hardcoded_agent")
        self.assertNotIn("mate.agent.config_hash", attrs)


def _turn(trace_id, version, version_id, config_hash, start="2026-10-25T10:00:00"):
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    later = t0.replace(second=1)
    spans = [
        {"span_id": "a", "parent_span_id": None, "name": "invoke_agent support",
         "start_time": t0, "end_time": later, "status": "OK", "error_message": None,
         "attributes": {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "support"}},
        {"span_id": "b", "parent_span_id": "a", "name": "gen_ai.inference",
         "start_time": t0, "end_time": later, "status": "OK", "error_message": None,
         "attributes": {"mate.agent.name": "support", "gen_ai.request.model": "m",
                        "mate.agent.config_hash": config_hash,
                        **({"mate.agent.config_version": version,
                            "mate.agent.config_version_id": version_id} if version else {})}},
    ]
    return build_turn(trace_id, spans)


class TestInTheReplay(_Db):

    def test_each_model_call_says_which_version_answered(self):
        turn = _turn("t1", 12, 41, "abc")
        llm = next(e for e in turn["events"] if e["kind"] == "llm")
        self.assertEqual((llm["config_version"], llm["config_version_id"], llm["config_hash"]), (12, 41, "abc"))
        self.assertEqual(turn["agents"]["support"]["configs"],
                         [{"hash": "abc", "version": 12, "version_id": 41}])

    def test_two_turns_on_different_versions_show_both(self):
        timeline = build_timeline([_turn("t1", 12, 41, "abc"),
                                   _turn("t2", 13, 42, "def", start="2026-10-25T10:05:00")])
        self.assertEqual([c["version"] for c in timeline["agents"]["support"]["configs"]], [12, 13])

    def test_an_unversioned_config_shows_its_hash(self):
        turn = _turn("t1", None, None, "fff")
        self.assertEqual(turn["agents"]["support"]["configs"],
                         [{"hash": "fff", "version": None, "version_id": None}])

    def test_the_replay_names_each_agents_config_and_latest_version(self):
        from shared.utils.dashboard.dashboard_server import DashboardServer
        self._save_version(1)
        self._save_version(2)
        server = DashboardServer.__new__(DashboardServer)
        server.AgentConfig, server.AgentConfigVersion = AgentConfig, AgentConfigVersion
        session = self.Session()
        try:
            configs = server._replay_agent_configs(session, {"support": {}, "gone": {}})
        finally:
            session.close()
        self.assertEqual(configs, {"support": {"config_id": self.agent_id, "latest_version": 2}})


if __name__ == "__main__":
    unittest.main()
