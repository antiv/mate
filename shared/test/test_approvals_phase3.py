#!/usr/bin/env python3
"""
Approvals inbox, phase 3 (documents/design/approvals-inbox.md): the agent's
approver, notify and expires_after_minutes settings, and each request answered
once, by whoever may answer it.
"""

import asyncio
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.adk.events.event import Event
from google.genai import types

from shared.test.test_approval_requests import CAMEL_ARGS, _Database
from shared.utils import approval_config, approval_notify, approval_service
from shared.utils.models import AgentConfig, ApprovalRequest


class TestConfig(unittest.TestCase):

    def test_the_list_is_the_chat_answering(self):
        config = approval_config.parse({"require_confirmation": ["issue_refund"]})
        self.assertEqual((config.tools, config.approver, config.notify, config.expires_after_minutes),
                         (["issue_refund"], "chat", None, None))

    def test_the_object(self):
        config = approval_config.parse(json.dumps({"require_confirmation": {
            "tools": ["issue_refund"], "approver": "inbox", "expires_after_minutes": 30,
            "notify": {"type": "slack", "url": "https://hooks.slack.example/x"}}}))
        self.assertEqual((config.tools, config.approver, config.expires_after_minutes),
                         (["issue_refund"], "inbox", 30))
        self.assertEqual(config.notify["type"], "slack")

    def test_what_does_not_validate_is_read_with_defaults(self):
        config = approval_config.parse({"require_confirmation": {
            "tools": "issue_refund", "approver": "boss", "expires_after_minutes": -5,
            "notify": {"type": "pager"}}})
        self.assertEqual((config.tools, config.approver, config.notify, config.expires_after_minutes),
                         (["issue_refund"], "chat", None, None))
        self.assertEqual(approval_config.parse("not json").tools, [])
        self.assertEqual(approval_config.parse({"require_confirmation": "one_tool"}).tools, ["one_tool"])

    def test_validation(self):
        validate = approval_config.validate
        self.assertEqual(validate({"require_confirmation": ["a"]}), [])
        self.assertEqual(validate({}), [])
        self.assertEqual(validate({"require_confirmation": {"tools": ["a"], "approver": "either"}}), [])
        errors = validate({"require_confirmation": {"tools": [], "approver": "boss", "when": 1,
                                                    "expires_after_minutes": 0,
                                                    "notify": {"type": "email"}}})
        joined = " | ".join(errors)
        for expected in ("unknown field(s) when", "tools must be", "approver must be",
                         "expires_after_minutes must be", "notify.to is required"):
            self.assertIn(expected, joined)
        self.assertIn("notify.url is required for slack",
                      " ".join(validate({"require_confirmation": {"tools": ["a"], "notify": {"type": "slack"}}})))
        self.assertTrue(validate({"require_confirmation": {"tools": ["a"], "expires_after_minutes": True}}))

    def test_saving_an_agent_refuses_a_bad_entry(self):
        from fastapi import HTTPException
        from shared.utils.dashboard.dashboard_server import DashboardServer
        with self.assertRaises(HTTPException) as caught:
            DashboardServer._refuse_bad_approval_config(
                {"tool_config": json.dumps({"require_confirmation": {"tools": ["a"], "approver": "x"}})})
        self.assertEqual(caught.exception.status_code, 400)
        DashboardServer._refuse_bad_approval_config({"tool_config": None})


class _Agent(_Database):
    """The refunds agent with approval settings, and the notification caught."""

    def setUp(self):
        super().setUp()
        sender = patch.object(approval_notify, "send_in_background")
        self.notified = sender.start()
        self.addCleanup(sender.stop)

    def configure(self, **settings):
        session = self.Session()
        agent = session.query(AgentConfig).filter_by(name="refunds").one()
        agent.tool_config = json.dumps({"require_confirmation": {"tools": ["issue_refund"], **settings}})
        session.commit()
        session.close()

    def record(self, **overrides):
        overrides.setdefault("args", copy.deepcopy(CAMEL_ARGS))
        return super().record(**overrides)

    def gate(self, confirmed=True, **overrides):
        kwargs = dict(session_id="s1", confirmation_call_id="adk-c1", confirmed=confirmed,
                      answered_by="alice")
        kwargs.update(overrides)
        return approval_service.gate_answer(**kwargs)


class TestRecording(_Agent):

    def test_the_agents_settings_are_kept_on_the_row(self):
        self.configure(approver="inbox", expires_after_minutes=30)
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        self.record()
        row, = self.rows()
        self.assertEqual(row.approver, "inbox")
        self.assertLess(abs((row.expires_at - before) - timedelta(minutes=30)), timedelta(minutes=1))

    def test_without_settings_the_chat_answers(self):
        self.record()
        row, = self.rows()
        self.assertEqual(row.approver, "chat")
        self.notified.assert_not_called()
        self.assertEqual(approval_service.answering(row.id), "chat")

    def test_a_request_the_inbox_answers_is_announced_without_argument_values(self):
        self.configure(approver="inbox", notify={"type": "slack", "url": "https://hooks.example/x"})
        row_id = self.record()
        request, destination = self.notified.call_args.args
        self.assertEqual((request["id"], request["tool_name"], destination["type"]),
                         (row_id, "issue_refund", "slack"))
        self.assertNotIn("A-7", json.dumps(request))
        self.assertEqual(approval_service.answering(row_id), "inbox")

    def test_a_run_nobody_chats_in_is_announced_whatever_the_approver(self):
        self.record(user_id="trigger_runner")
        self.assertEqual(self.rows()[0].source, "trigger")
        self.notified.assert_called_once()

    def test_either(self):
        self.configure(approver="either")
        row_id = self.record()
        self.assertEqual(approval_service.answering(row_id), "either")
        self.notified.assert_called_once()

    def test_a_sub_agent_takes_its_roots_settings(self):
        self.configure(approver="inbox")
        self.record(agent_name="subagent_abc123")
        self.assertEqual(self.rows()[0].approver, "inbox")


class TestGate(_Agent):

    def test_the_chat_answers_once(self):
        self.record()
        self.assertIs(self.gate(confirmed=False), False)
        self.assertIsNone(self.gate(confirmed=True))
        row, = self.rows()
        self.assertEqual((row.status, row.decided_via), ("rejected", "chat"))
        self.assertIsNotNone(row.answered_at)

    def test_the_chat_may_not_answer_what_the_inbox_answers(self):
        self.configure(approver="inbox")
        self.record()
        self.assertIsNone(self.gate())
        self.assertEqual(self.rows()[0].status, "pending")

    def test_nor_a_run_nobody_chats_in(self):
        self.record(user_id="trigger_runner")
        self.assertIsNone(self.gate())
        self.assertEqual(self.rows()[0].status, "pending")

    def test_either_lets_the_chat_answer(self):
        self.configure(approver="either")
        self.record()
        self.assertIs(self.gate(), True)
        self.assertEqual(self.rows()[0].decided_via, "chat")

    def test_the_inbox_decision_reaches_the_run_once_and_as_decided(self):
        self.configure(approver="inbox")
        row_id = self.record()
        status, _ = approval_service.claim_decision(row_id, decision="reject", decided_by="admin", note=None)
        self.assertEqual(status, 200)
        # A forged approval arrives first: the run gets the rejection
        self.assertIs(self.gate(confirmed=True), False)
        # The inbox's own answer then has nothing left to deliver
        self.assertIsNone(self.gate(confirmed=False))

    def test_a_cancelled_request_takes_no_answer(self):
        self.record()
        session = self.Session()
        approval_service.cancel_in_transaction(session, ["refunds"])
        session.commit()
        session.close()
        self.assertIsNone(self.gate())

    def test_a_call_with_no_row_passes_as_given(self):
        self.assertIs(self.gate(confirmed=False, confirmation_call_id="unknown"), False)


class TestInbox(_Agent):

    def test_the_inbox_decides_a_chats_request_when_the_agent_says_so(self):
        for approver, expected in (("chat", 409), ("inbox", 200), ("either", 200)):
            with self.subTest(approver=approver):
                self.configure(approver=approver)
                row_id = self.record(confirmation_call_id=f"c-{approver}")
                status, _ = approval_service.claim_decision(row_id, decision="approve",
                                                            decided_by="admin", note=None)
                self.assertEqual(status, expected)

    def test_expiry_takes_what_the_inbox_answers(self):
        self.configure(approver="inbox")
        self.record(confirmation_call_id="inbox-one")
        self.configure(approver="chat")
        self.record(confirmation_call_id="chat-one")
        later = datetime.now(timezone.utc) + timedelta(days=30)
        expired = approval_service.expire_due(now=later)
        self.assertEqual([r["confirmation_call_id"] for r in expired], ["inbox-one"])


class TestAdkPlugin(_Agent):

    def setUp(self):
        super().setUp()
        from shared.callbacks.approval_plugin import ApprovalCapturePlugin
        self.plugin = ApprovalCapturePlugin()
        self.ctx = SimpleNamespace(session=SimpleNamespace(id="s1", state={}),
                                   app_name="refunds", user_id="u1", invocation_id="e-1")

    def answer(self, confirmed):
        message = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="adk-c1", name="adk_request_confirmation", response={"confirmed": confirmed}))])
        return asyncio.run(self.plugin.on_user_message_callback(invocation_context=self.ctx,
                                                                user_message=message))

    def pause(self):
        args = copy.deepcopy(CAMEL_ARGS)
        event = Event(author="refunds", invocation_id="e-1", content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(id="adk-c1", name="adk_request_confirmation",
                                                        args=args))]))
        asyncio.run(self.plugin.on_event_callback(invocation_context=self.ctx, event=event))
        return event.content.parts[0].function_call.args

    def test_the_chat_learns_the_inbox_answers(self):
        self.configure(approver="inbox")
        self.assertEqual(self.pause()["mateApprover"], "inbox")

    def test_a_chats_own_request_is_left_as_it_was(self):
        self.assertNotIn("mateApprover", self.pause())

    def test_an_answer_the_chat_may_not_give_becomes_text(self):
        self.configure(approver="inbox")
        self.pause()
        replaced = self.answer(True)
        self.assertEqual([p.function_response for p in replaced.parts], [None])
        self.assertIn("approvals inbox", replaced.parts[0].text)

    def test_the_inbox_decision_replaces_the_answer(self):
        self.configure(approver="inbox")
        self.pause()
        row, = self.rows()
        approval_service.claim_decision(row.id, decision="reject", decided_by="admin", note=None)
        replaced = self.answer(True)
        self.assertEqual(replaced.parts[0].function_response.response, {"confirmed": False})

    def test_an_answer_as_given_is_left_alone(self):
        self.pause()
        self.assertIsNone(self.answer(True))


class TestNotify(unittest.TestCase):

    REQUEST = {"id": 7, "agent_name": "refunds", "tool_name": "issue_refund", "source": "trigger",
               "workspace_id": None, "arg_names": ["amount", "order"], "expires_at": None}

    def test_the_default_destination_is_the_default_workspaces(self):
        env = {"APPROVALS_NOTIFY_TYPE": "slack", "APPROVALS_NOTIFY_URL": "https://hooks.example/x"}
        with patch.dict(os.environ, env):
            self.assertEqual(approval_notify.default_destination(None)["type"], "slack")
            self.assertEqual(approval_notify.default_destination(1)["url"], "https://hooks.example/x")
            self.assertIsNone(approval_notify.default_destination(2))
        with patch.dict(os.environ, {"APPROVALS_NOTIFY_TYPE": "email", "APPROVALS_NOTIFY_TO": ""}):
            self.assertIsNone(approval_notify.default_destination(None))

    def test_the_message(self):
        with patch.dict(os.environ, {"MATE_PUBLIC_URL": "https://mate.example/"}), \
                patch("shared.utils.notify.deliver", return_value=(True, "ok")) as deliver:
            self.assertEqual(approval_notify.send(self.REQUEST, {"type": "discord", "url": "https://d.example"}),
                             (True, "ok"))
        kind, config = deliver.call_args.args
        kwargs = deliver.call_args.kwargs
        self.assertEqual(kind, "discord")
        self.assertIn("https://mate.example/dashboard/approvals?id=7", kwargs["text"])
        self.assertIn("amount, order", kwargs["text"])
        self.assertEqual(kwargs["payload"]["event"], "approval.requested")

    def test_nowhere_to_send(self):
        with patch.dict(os.environ, {"APPROVALS_NOTIFY_TYPE": ""}):
            self.assertEqual(approval_notify.send(self.REQUEST, None), (False, "no destination"))


if __name__ == "__main__":
    unittest.main()
