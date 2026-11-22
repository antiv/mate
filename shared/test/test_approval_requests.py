#!/usr/bin/env python3
"""
Approvals inbox, phase 1 (documents/design/approvals-inbox.md): the approval_requests
table (V050), filled from both runtimes, with an audit entry for each decision made
in a chat. Nothing here changes how a run goes.
"""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.adk.events.event import Event
from google.genai import types
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import approval_service
from shared.utils.models import AgentConfig, ApprovalRequest, AuditLog, Base, Project, Workspace

CAMEL_ARGS = {
    "originalFunctionCall": {"id": "call_1", "name": "issue_refund", "args": {"order": "A-7", "amount": 40}},
    "toolConfirmation": {"hint": "Please approve or reject the tool call issue_refund()", "confirmed": False},
}
SNAKE_ARGS = {
    "original_function_call": {"id": "call_1", "name": "issue_refund", "args": {"order": "A-7", "amount": 40}},
    "tool_confirmation": {"hint": "Please approve or reject the tool call issue_refund()"},
}


class _Database(unittest.TestCase):
    """A file database with a workspace, a project in it and its agent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine = create_engine(f"sqlite:///{os.path.join(tmp.name, 'mate.db')}",
                               connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        session = self.Session()
        session.add(Workspace(id=2, name="B", slug="b"))  # create_all made the default one
        session.flush()
        session.add(Project(id=5, name="support", workspace_id=2))
        session.flush()
        session.add(AgentConfig(name="refunds", type="llm", project_id=5))
        session.commit()
        session.close()
        db = MagicMock()
        db.is_connected.return_value = True
        db.get_session.side_effect = lambda: self.Session()
        for target in ("shared.utils.approval_service.get_database_client",
                       "shared.utils.audit_service.get_database_client"):
            patcher = patch(target, return_value=db)
            patcher.start()
            self.addCleanup(patcher.stop)

    def record(self, **overrides):
        kwargs = dict(confirmation_call_id="adk-c1", args=CAMEL_ARGS, agent_name="refunds",
                      app_name="refunds", user_id="u1", session_id="s1", invocation_id="e-1")
        kwargs.update(overrides)
        return approval_service.record_request(**kwargs)

    def rows(self, model=ApprovalRequest, **filters):
        session = self.Session()
        try:
            return session.query(model).filter_by(**filters).all()
        finally:
            session.close()


class TestParse(unittest.TestCase):

    def test_both_casings_name_the_same_call(self):
        camel = approval_service.parse_confirmation_call("adk-c1", CAMEL_ARGS)
        snake = approval_service.parse_confirmation_call("adk-c1", SNAKE_ARGS)
        self.assertEqual(camel, snake)
        self.assertEqual(camel, {"confirmation_call_id": "adk-c1", "tool_call_id": "call_1",
                                 "tool_name": "issue_refund",
                                 "tool_args": {"order": "A-7", "amount": 40},
                                 "hint": "Please approve or reject the tool call issue_refund()"})

    def test_a_call_naming_no_tool_is_not_a_request(self):
        self.assertIsNone(approval_service.parse_confirmation_call("adk-c1", {}))
        self.assertIsNone(approval_service.parse_confirmation_call("adk-c1", None))
        self.assertIsNone(approval_service.parse_confirmation_call(None, CAMEL_ARGS))

    def test_long_arguments_are_cut(self):
        stored = approval_service.serialize_args({"text": "x" * 50_000})
        self.assertEqual(len(stored), approval_service.MAX_ARGS_CHARS + 1)
        self.assertTrue(stored.endswith("…"))

    def test_the_digest_is_of_the_stored_arguments(self):
        stored = approval_service.serialize_args({"b": 1, "a": 2})
        self.assertEqual(stored, '{"a": 2, "b": 1}')
        self.assertEqual(len(approval_service.args_digest(stored)), 64)
        self.assertIsNone(approval_service.args_digest(None))

    def test_wire_parts_yield_only_confirmation_calls(self):
        parts = [{"text": "hi"},
                 {"functionCall": {"id": "call_1", "name": "issue_refund", "args": {}}},
                 {"functionCall": {"id": "adk-c1", "name": "adk_request_confirmation", "args": CAMEL_ARGS}},
                 {"function_call": {"id": "adk-c2", "name": "adk_request_confirmation", "args": SNAKE_ARGS}}]
        self.assertEqual([c["id"] for c in approval_service.confirmation_calls(parts)],
                         ["adk-c1", "adk-c2"])


class TestRecordRequest(_Database):

    def test_a_pause_becomes_a_pending_row_in_the_agents_workspace(self):
        row_id = self.record()
        row, = self.rows()
        self.assertEqual(row.id, row_id)
        self.assertEqual((row.status, row.agent_name, row.app_name, row.user_id, row.session_id),
                         ("pending", "refunds", "refunds", "u1", "s1"))
        self.assertEqual((row.project_id, row.workspace_id), (5, 2))
        self.assertEqual((row.confirmation_call_id, row.tool_call_id, row.tool_name),
                         ("adk-c1", "call_1", "issue_refund"))
        self.assertEqual(json.loads(row.tool_args), {"amount": 40, "order": "A-7"})
        self.assertEqual(row.invocation_id, "e-1")
        self.assertEqual(row.source, "chat")
        self.assertIsNone(row.decided_at)

    def test_the_source_comes_from_the_run(self):
        self.record(user_id="trigger_runner", session_id="s-t", trigger_id="7")
        self.record(session_id="openai_sess_x")
        trigger, = self.rows(session_id="s-t")
        self.assertEqual((trigger.source, trigger.trigger_id), ("trigger", 7))
        self.assertEqual(self.rows(session_id="openai_sess_x")[0].source, "api")

    def test_the_same_call_is_recorded_once(self):
        first = self.record()
        self.assertEqual(self.record(), first)
        self.assertEqual(len(self.rows()), 1)

    def test_the_same_id_in_another_session_is_another_row(self):
        # LangGraph confirmation ids come from the model's tool call ids
        self.record()
        self.record(session_id="s2")
        self.assertEqual(len(self.rows()), 2)

    def test_an_agent_the_database_does_not_know_takes_its_roots_scope(self):
        self.record(agent_name="hardcoded_helper")
        row, = self.rows()
        self.assertEqual((row.agent_name, row.workspace_id), ("hardcoded_helper", 2))

    def test_no_database_records_nothing_and_does_not_raise(self):
        with patch("shared.utils.approval_service.get_database_client", return_value=None):
            self.assertIsNone(self.record())


class TestRecordDecision(_Database):

    def decide(self, confirmed=True, **overrides):
        kwargs = dict(session_id="s1", confirmation_call_id="adk-c1", confirmed=confirmed,
                      decided_by="alice")
        kwargs.update(overrides)
        return approval_service.record_decision(**kwargs)

    def test_an_approval_in_the_chat_is_recorded_and_audited(self):
        row_id = self.record()
        self.assertTrue(self.decide())
        row, = self.rows()
        self.assertEqual((row.status, row.decided_by, row.decided_via), ("approved", "alice", "chat"))
        self.assertIsNotNone(row.decided_at)
        entry, = self.rows(AuditLog)
        self.assertEqual((entry.actor, entry.action, entry.resource_type, entry.resource_id),
                         ("alice", "approval.approved", "approval_request", str(row_id)))
        self.assertEqual((entry.project_id, entry.workspace_id), (5, 2))
        details = entry.get_details()
        self.assertEqual(details["tool"], "issue_refund")
        self.assertEqual(details["args_sha256"], approval_service.args_digest(row.tool_args))
        # The arguments themselves stay in the row
        self.assertNotIn("A-7", entry.details)

    def test_a_rejection(self):
        self.record()
        self.assertTrue(self.decide(confirmed=False))
        self.assertEqual(self.rows()[0].status, "rejected")
        self.assertEqual(self.rows(AuditLog)[0].action, "approval.rejected")

    def test_a_second_decision_changes_nothing(self):
        self.record()
        self.assertTrue(self.decide())
        self.assertFalse(self.decide(confirmed=False, decided_by="bob"))
        row, = self.rows()
        self.assertEqual((row.status, row.decided_by), ("approved", "alice"))
        self.assertEqual(len(self.rows(AuditLog)), 1)

    def test_an_answer_with_no_row_changes_nothing(self):
        self.assertFalse(self.decide(confirmation_call_id="adk-unknown"))
        self.record()
        self.assertFalse(self.decide(session_id="another-session"))
        self.assertEqual(self.rows()[0].status, "pending")
        self.assertEqual(self.rows(AuditLog), [])


class TestAdkPlugin(_Database):
    """ApprovalCapturePlugin sees the pause as an event and the answer as a user message."""

    def setUp(self):
        super().setUp()
        from shared.callbacks.approval_plugin import ApprovalCapturePlugin
        self.plugin = ApprovalCapturePlugin()
        self.ctx = SimpleNamespace(
            session=SimpleNamespace(id="s1", state={approval_service.TRIGGER_STATE_KEY: 9}),
            app_name="refunds", user_id="trigger_runner", invocation_id="e-1")

    def test_a_confirmation_event_is_recorded_and_left_unchanged(self):
        event = Event(author="refunds", invocation_id="e-1", content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(id="call_1", name="issue_refund", args={})),
            types.Part(function_call=types.FunctionCall(
                id="adk-c1", name="adk_request_confirmation", args=CAMEL_ARGS)),
        ]))
        self.assertIsNone(asyncio.run(self.plugin.on_event_callback(
            invocation_context=self.ctx, event=event)))
        row, = self.rows()
        self.assertEqual((row.confirmation_call_id, row.tool_name, row.source, row.trigger_id),
                         ("adk-c1", "issue_refund", "trigger", 9))

    def test_other_events_record_nothing(self):
        event = Event(author="refunds", content=types.Content(role="model", parts=[types.Part(text="hi")]))
        asyncio.run(self.plugin.on_event_callback(invocation_context=self.ctx, event=event))
        self.assertEqual(self.rows(), [])

    def test_the_answer_in_the_users_message_decides_the_row(self):
        self.record()
        message = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="adk-c1", name="adk_request_confirmation", response={"confirmed": False}))])
        self.ctx.user_id = "alice"
        self.assertIsNone(asyncio.run(self.plugin.on_user_message_callback(
            invocation_context=self.ctx, user_message=message)))
        row, = self.rows()
        self.assertEqual((row.status, row.decided_by), ("rejected", "alice"))

    def test_a_failing_database_does_not_reach_the_run(self):
        event = Event(author="refunds", content=types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(id="adk-c1", name="adk_request_confirmation", args=CAMEL_ARGS))]))
        with patch.object(approval_service, "record_request", side_effect=RuntimeError("db down")):
            self.assertIsNone(asyncio.run(self.plugin.on_event_callback(
                invocation_context=self.ctx, event=event)))

    def test_registered_whatever_mate_plugins_enabled_says(self):
        import inspect
        from shared.utils import utils
        source = inspect.getsource(utils)
        self.assertLess(source.index("ApprovalCapturePlugin()"),
                        source.index('MATE_PLUGINS_ENABLED", "false"'))


class TestLangGraph(_Database):
    """The executor records the pause as it stores the event, and the answer it resumes on."""

    def test_a_confirmation_event_is_recorded(self):
        from shared.utils.langgraph.executor import _record_requests
        event = {"author": "refunds", "content": {"role": "model", "parts": [{"functionCall": {
            "id": "adk-confirm-call_1", "name": "adk_request_confirmation", "args": CAMEL_ARGS}}]}}
        asyncio.run(_record_requests(event, "refunds", "u1", "s1", "e-1",
                                     {approval_service.TRIGGER_STATE_KEY: 3}))
        row, = self.rows()
        self.assertEqual((row.confirmation_call_id, row.tool_call_id, row.trigger_id),
                         ("adk-confirm-call_1", "call_1", 3))

    def test_the_answer_decides_the_row(self):
        from shared.utils.langgraph.executor import _gate_decisions
        self.record(confirmation_call_id="adk-confirm-call_1")
        allowed = asyncio.run(_gate_decisions({"adk-confirm-call_1": True}, "s1", "alice"))
        self.assertEqual(allowed, {"adk-confirm-call_1": True})
        self.assertEqual(self.rows()[0].status, "approved")


class TestTriggerRunner(unittest.TestCase):

    def test_the_session_names_the_trigger(self):
        from shared.utils import trigger_runner
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = MagicMock(status_code=200, json=lambda: {"id": "s1"})
        client.stream.return_value.__enter__.return_value = MagicMock(status_code=500)
        with patch.object(trigger_runner.httpx, "Client", return_value=client):
            with self.assertRaises(RuntimeError):
                trigger_runner.TriggerRunner()._invoke_agent("refunds", "go", trigger_id=7)
        self.assertEqual(client.post.call_args.kwargs["json"],
                         {"state": {approval_service.TRIGGER_STATE_KEY: 7}})


class TestMigration(unittest.TestCase):
    """V050 on SQLite."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = os.path.join(tmp.name, "mate.db")
        patcher = patch.dict(os.environ, {"DB_TYPE": "sqlite", "DB_PATH": self.db_path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def migrate(self):
        from shared.utils.migration_system import MigrationSystem
        self.assertTrue(MigrationSystem().run_migrations())

    def test_a_call_is_unique_within_its_session(self):
        self.migrate()
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)
        insert = ("INSERT INTO approval_requests (agent_name, app_name, user_id, session_id,"
                  " confirmation_call_id, tool_name) VALUES ('a', 'a', 'u', ?, 'c1', 't')")
        conn.execute(insert, ("s1",))
        conn.execute(insert, ("s2",))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(insert, ("s1",))
        status, source = conn.execute("SELECT status, source FROM approval_requests LIMIT 1").fetchone()
        self.assertEqual((status, source), ("pending", "chat"))

    def test_a_create_all_schema_matches(self):
        engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()
        self.migrate()
        session = sessionmaker(bind=create_engine(f"sqlite:///{self.db_path}"))()
        self.addCleanup(session.close)
        session.add(ApprovalRequest(agent_name="a", app_name="a", user_id="u", session_id="s",
                                    confirmation_call_id="c", tool_name="t"))
        session.commit()
        self.assertEqual(session.query(ApprovalRequest).one().status, "pending")


if __name__ == "__main__":
    unittest.main()
