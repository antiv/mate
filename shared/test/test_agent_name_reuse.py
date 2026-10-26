#!/usr/bin/env python3
"""
Security fixes found in the 1.5.1/1.6.0 reviews (#276, "for later").

1. Deleting an agent left the rows that refer to it by name, so the next agent
   created with that name inherited them: users' stored OAuth credentials, their
   conversations (ADK and LangGraph) and memory, file search store links (which
   may point at another project's stores), widget keys, chat integrations,
   triggers, budgets, alert rules and test cases. Every delete path now removes
   them (shared/utils/agent_cleanup.py).
2. The rate-limit middleware counted a /run_sse request against the user id in
   its body before the proxy checked that id, so a signed-in user could use up
   another user's allowance with requests that were then refused.
3. Deleting a LangGraph session left its checkpointed graph state, so a session
   created later with the same id resumed the old conversation.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import agent_cleanup
from shared.utils.agent_cleanup import delete_agent_rows
from shared.utils.models import (AgentConfig, AgentConfigVersion, AgentFileSearchStore, AgentTrigger,
                                 AlertRule, Base, ChannelIntegration, Credential, EvalResult,
                                 LangGraphEvent, LangGraphSession, MemoryEvent, MemorySession,
                                 Project, RateLimitConfig, TestCase, TokenUsageLog, WidgetApiKey)

ROOT = Path(__file__).resolve().parent.parent.parent

# What ADK's DatabaseSessionService creates, reduced to the columns that matter here
ADK_TABLES = (
    "CREATE TABLE sessions (app_name VARCHAR(128), user_id VARCHAR(128), id VARCHAR(128), state TEXT)",
    "CREATE TABLE events (id VARCHAR(128), app_name VARCHAR(128), user_id VARCHAR(128), session_id VARCHAR(128))",
    "CREATE TABLE app_states (app_name VARCHAR(128), state TEXT)",
    "CREATE TABLE user_states (app_name VARCHAR(128), user_id VARCHAR(128), state TEXT)",
)

# Per table, a count query for the rows of one agent name
NAMED = {
    "agent_file_search_stores": (AgentFileSearchStore, lambda n: AgentFileSearchStore.agent_name == n),
    "widget_api_keys": (WidgetApiKey, lambda n: WidgetApiKey.agent_name == n),
    "channel_integrations": (ChannelIntegration, lambda n: ChannelIntegration.agent_name == n),
    "agent_triggers": (AgentTrigger, lambda n: AgentTrigger.agent_name == n),
    "rate_limit_config": (RateLimitConfig, lambda n: (RateLimitConfig.scope == "agent") & (RateLimitConfig.scope_id == n)),
    "alert_rules": (AlertRule, lambda n: (AlertRule.scope == "agent") & (AlertRule.scope_id == n)),
    "test_cases": (TestCase, lambda n: TestCase.agent_name == n),
    "credentials": (Credential, lambda n: Credential.app_name == n),
    "memory_sessions": (MemorySession, lambda n: MemorySession.app_name == n),
    "lg_sessions": (LangGraphSession, lambda n: LangGraphSession.app_name == n),
}


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            for ddl in ADK_TABLES:
                conn.execute(text(ddl))
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        self.db = MagicMock()
        self.db.get_session.side_effect = lambda: self.Session()
        session = self.Session()
        session.add_all([Project(id=1, name="p1"), Project(id=2, name="p2")])
        session.commit()
        session.close()
        for name, project in (("bot", 1), ("keep", 1)):
            self._add_agent_rows(name, project)

    def _add_agent_rows(self, name, project_id):
        session = self.Session()
        agent = AgentConfig(name=name, type="llm", model_name="m", project_id=project_id)
        session.add(agent)
        session.flush()
        session.add(AgentConfigVersion(agent_config_id=agent.id, version_number=1, config_snapshot="{}"))
        session.add_all([
            AgentFileSearchStore(agent_name=name, store_id=99),
            WidgetApiKey(api_key=f"pk-{name}", agent_name=name, project_id=project_id),
            ChannelIntegration(platform="slack", project_id=project_id, agent_name=name),
            AgentTrigger(name="t", agent_name=name, project_id=project_id, prompt="p",
                         webhook_path=f"hook-{name}"),
            RateLimitConfig(scope="agent", scope_id=name),
            AlertRule(name="a", scope="agent", scope_id=name, condition_type="agent_error_count",
                      destination_type="http"),
            Credential(app_name=name, user_id="u1", credential_key="google", credential_data="{}"),
            MemorySession(session_id=f"mem-{name}", app_name=name, user_id="u1"),
            LangGraphSession(id=f"lg-{name}", app_name=name, user_id="u1"),
            TokenUsageLog(request_id=f"r-{name}", agent_name=name, model_name="m", status="SUCCESS"),
        ])
        session.flush()
        case = TestCase(agent_name=name, input="q", expected_output="a", eval_method="exact_match")
        session.add(case)
        session.flush()
        version_id = session.query(AgentConfigVersion.id).filter(
            AgentConfigVersion.agent_config_id == agent.id).scalar()
        session.add(EvalResult(test_case_id=case.id, version_id=version_id, actual_output="a",
                               passed=True, eval_method="exact_match"))
        session.add(MemoryEvent(session_id=f"mem-{name}", event_id="e1", author="user", content={"t": "hi"}))
        session.add(LangGraphEvent(id=f"ev-{name}", session_id=f"lg-{name}"))
        session.commit()
        session.close()
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO sessions VALUES (:n, 'u1', :s, '{}')"), {"n": name, "s": f"adk-{name}"})
            conn.execute(text("INSERT INTO events VALUES (:e, :n, 'u1', :s)"),
                         {"e": f"e-{name}", "n": name, "s": f"adk-{name}"})
            conn.execute(text("INSERT INTO app_states VALUES (:n, '{}')"), {"n": name})
            conn.execute(text("INSERT INTO user_states VALUES (:n, 'u1', '{}')"), {"n": name})

    def _left(self, name):
        """The tables that still hold rows of this agent name."""
        session = self.Session()
        try:
            left = [table for table, (model, where) in NAMED.items()
                    if session.query(model).filter(where(name)).count()]
            for table in ("sessions", "events", "app_states", "user_states"):
                if session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE app_name = :n"),
                                   {"n": name}).scalar():
                    left.append(f"adk_{table}")
            return sorted(left)
        finally:
            session.close()


class TestDeleteAgentRows(_Db):

    def test_every_row_of_the_agent_goes_and_no_other(self):
        session = self.Session()
        removed = delete_agent_rows(session, ["bot"])
        session.commit()
        session.close()
        self.assertEqual(self._left("bot"), [])
        self.assertEqual(len(self._left("keep")), len(NAMED) + 4)
        self.assertEqual(removed["langgraph_threads"], ["lg-bot"])
        session = self.Session()
        self.assertEqual(session.query(MemoryEvent).count(), 1)
        self.assertEqual(session.query(EvalResult).count(), 1)
        self.assertEqual(session.query(LangGraphEvent).count(), 1)
        # Logs stay
        self.assertEqual(session.query(TokenUsageLog).filter(TokenUsageLog.agent_name == "bot").count(), 1)
        session.close()

    def test_no_names_delete_nothing(self):
        session = self.Session()
        self.assertEqual(delete_agent_rows(session, []), {"langgraph_threads": []})
        session.close()
        self.assertEqual(len(self._left("bot")), len(NAMED) + 4)

    def test_a_database_without_adk_tables(self):
        with self.engine.begin() as conn:
            for table in ("events", "sessions", "app_states", "user_states"):
                conn.execute(text(f"DROP TABLE {table}"))
        session = self.Session()
        delete_agent_rows(session, ["bot"])
        session.commit()
        session.close()


class TestEveryDeletePath(_Db):

    def _server(self):
        from shared.utils.dashboard.dashboard_server import DashboardServer
        from fastapi import FastAPI as _F
        with patch("shared.utils.database_client.get_database_client", return_value=self.db):
            server = DashboardServer(_F(), project_root=ROOT)
        server.db_client = self.db
        return server

    def _agent_id(self, name):
        session = self.Session()
        try:
            return session.query(AgentConfig.id).filter(AgentConfig.name == name).scalar()
        finally:
            session.close()

    def test_the_dashboard_agent_delete(self):
        self.assertTrue(self._server()._delete_agent_config(self._agent_id("bot")))
        self.assertEqual(self._left("bot"), [])
        self.assertTrue(self._left("keep"))

    def test_a_new_agent_with_the_name_starts_clean(self):
        self._server()._delete_agent_config(self._agent_id("bot"))
        session = self.Session()
        session.add(AgentConfig(name="bot", type="llm", model_name="m", project_id=2))
        session.commit()
        session.close()
        self.assertEqual(self._left("bot"), [])

    def test_the_project_delete(self):
        self._add_agent_rows("other", 2)
        self._server()._delete_project(1)
        self.assertEqual(self._left("bot"), [])
        self.assertEqual(self._left("keep"), [])
        self.assertTrue(self._left("other"))

    def test_the_create_agent_tool(self):
        from shared.utils.tools import create_agent_tool
        context = MagicMock()
        context.agent_name = "root"
        with patch.object(create_agent_tool, "get_database_client", return_value=self.db), \
                patch.object(create_agent_tool, "reload_agent_cache", return_value={"success": True}), \
                patch.object(create_agent_tool, "_is_admin_user", return_value=True):
            result = create_agent_tool.delete_agent("bot", tool_context=context)
        self.assertEqual(result.get("status"), "success", result)
        self.assertEqual(self._left("bot"), [])
        session = self.Session()
        self.assertEqual(session.query(AgentConfigVersion).count(), 1)
        session.close()

    def test_the_wizard_trial_cleanup(self):
        from shared.utils.wizard import cleanup
        session = self.Session()
        cleanup._delete_trial_project(session, 1)
        session.commit()
        session.close()
        self.assertEqual(self._left("bot"), [])


class TestCheckpointsGoWithTheAgent(unittest.TestCase):

    def test_purge_removes_only_the_given_threads(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.checkpoint.base import empty_checkpoint
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lg_checkpoints.db"
            with SqliteSaver.from_conn_string(str(path)) as saver:
                for thread in ("lg-bot", "lg-keep"):
                    saver.put({"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
                              empty_checkpoint(), {}, {})
            with patch("shared.utils.langgraph.agent_builder.CHECKPOINT_DB_PATH", path):
                agent_cleanup.purge_langgraph_checkpoints(["lg-bot"])
            with SqliteSaver.from_conn_string(str(path)) as saver:
                self.assertIsNone(saver.get_tuple({"configurable": {"thread_id": "lg-bot"}}))
                self.assertIsNotNone(saver.get_tuple({"configurable": {"thread_id": "lg-keep"}}))

    def test_no_checkpoint_file_is_fine(self):
        with patch("shared.utils.langgraph.agent_builder.CHECKPOINT_DB_PATH", Path("/nonexistent/x.db")):
            agent_cleanup.purge_langgraph_checkpoints(["lg-bot"])


class TestRefusedRequestsAreNotCounted(unittest.TestCase):

    def setUp(self):
        from server import rate_limit_middleware
        from shared.utils.rate_limit_service import RateLimitResult
        self.service = MagicMock()
        self.service.check_request_limit = AsyncMock(
            return_value=(RateLimitResult(allowed=True, action="warn", message=""), None))
        self.service.record_request = AsyncMock()
        patcher = patch.object(rate_limit_middleware, "get_rate_limit_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.add_middleware(rate_limit_middleware.RateLimitMiddleware)
        self.status = 200

        @app.post("/run_sse")
        async def run_sse():
            # The proxy, downstream: 403 for "not your user id"
            return JSONResponse({"ok": self.status == 200}, status_code=self.status)

        self.client = TestClient(app)

    def _post(self):
        return self.client.post("/run_sse", content=json.dumps({"app_name": "bot", "user_id": "victim"}),
                                headers={"Content-Type": "application/json"})

    def test_a_request_the_proxy_refuses_is_not_counted(self):
        for status in (400, 401, 403):
            self.status = status
            self.assertEqual(self._post().status_code, status)
        self.service.record_request.assert_not_called()

    def test_a_request_that_runs_is_counted(self):
        self._post()
        self.service.record_request.assert_awaited_once_with(user_id="victim", agent_name="bot")


class TestLangGraphSessionCheckpoints(unittest.TestCase):

    def setUp(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from shared.utils.langgraph import agent_builder, session_store
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = MagicMock()
        db.get_session.side_effect = lambda: Session()
        patch.object(session_store, "get_database_client", return_value=db).start()
        patch.object(session_store, "_session_store", None).start()
        self.saver = InMemorySaver()
        patch.object(agent_builder, "_checkpointer", self.saver).start()
        self.addCleanup(patch.stopall)
        from shared.utils.langgraph.api import create_app
        self.client = TestClient(create_app())

    def _checkpoint(self, thread_id):
        from langgraph.checkpoint.base import empty_checkpoint
        self.saver.put({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                       empty_checkpoint(), {}, {})

    def _has_checkpoint(self, thread_id):
        return self.saver.get_tuple({"configurable": {"thread_id": thread_id}}) is not None

    def test_deleting_a_session_deletes_its_checkpoint(self):
        self.assertEqual(self.client.post("/apps/bot/users/u1/sessions/s1", json={}).status_code, 200)
        self._checkpoint("s1")
        self.assertEqual(self.client.delete("/apps/bot/users/u1/sessions/s1").status_code, 200)
        self.assertFalse(self._has_checkpoint("s1"))

    def test_a_new_session_does_not_resume_a_leftover_checkpoint(self):
        # Left by a session deleted before this fix
        self._checkpoint("s2")
        self.assertEqual(self.client.post("/apps/bot/users/u2/sessions/s2", json={}).status_code, 200)
        self.assertFalse(self._has_checkpoint("s2"))

    def test_someone_elses_session_is_not_deleted(self):
        self.client.post("/apps/bot/users/u1/sessions/s3", json={})
        self._checkpoint("s3")
        self.assertEqual(self.client.delete("/apps/bot/users/u2/sessions/s3").status_code, 404)
        self.assertTrue(self._has_checkpoint("s3"))


if __name__ == "__main__":
    unittest.main()
