#!/usr/bin/env python3
"""
Approvals inbox, phase 2 (documents/design/approvals-inbox.md): deciding a request in
the inbox, resuming its run, expiry, cancellation with the agent, a trigger's output
held until its requests are decided, and A2A and MCP runs told apart from a chat.
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.test.test_approval_requests import _Database
from shared.utils import approval_resume, approval_service, run_surface
from shared.utils.models import AgentTrigger, ApprovalRequest, AuditLog

TRIGGER = dict(user_id="trigger_runner", session_id="s-t", trigger_id=7)


class TestClaimDecision(_Database):

    def claim(self, request_id, decision="approve", **overrides):
        kwargs = dict(decision=decision, decided_by="alice", note=None)
        kwargs.update(overrides)
        return approval_service.claim_decision(request_id, **kwargs)

    def test_a_triggers_request_is_decided_and_audited(self):
        request_id = self.record(**TRIGGER)
        status, outcome = self.claim(request_id, note="  refund looks right ")
        self.assertEqual(status, 200)
        self.assertEqual((outcome["status"], outcome["confirmed"], outcome["session_id"]),
                         ("approved", True, "s-t"))
        row, = self.rows()
        self.assertEqual((row.status, row.decided_by, row.decided_via, row.note, row.resume_status),
                         ("approved", "alice", "inbox", "refund looks right", "resuming"))
        entry, = self.rows(AuditLog)
        self.assertEqual((entry.action, entry.actor), ("approval.approved", "alice"))
        self.assertEqual(entry.get_details()["decided_via"], "inbox")
        self.assertEqual(entry.get_details()["note"], "refund looks right")

    def test_a_rejection(self):
        request_id = self.record(**TRIGGER)
        status, outcome = self.claim(request_id, "reject")
        self.assertEqual((status, outcome["status"], outcome["confirmed"]), (200, "rejected", False))

    def test_of_two_decisions_one_wins(self):
        request_id = self.record(**TRIGGER)
        self.assertEqual(self.claim(request_id)[0], 200)
        status, message = self.claim(request_id, "reject", decided_by="bob")
        self.assertEqual(status, 409)
        self.assertEqual(self.rows()[0].decided_by, "alice")
        self.assertEqual(len(self.rows(AuditLog)), 1)

    def test_a_chats_request_is_answered_in_the_chat(self):
        request_id = self.record()
        self.assertEqual(self.claim(request_id)[0], 409)
        self.assertEqual(self.rows()[0].status, "pending")

    def test_another_workspaces_request_is_not_found_whatever_the_body(self):
        request_id = self.record(**TRIGGER)
        other = (ApprovalRequest.workspace_id == 99,)
        self.assertEqual(self.claim(request_id, row_filters=other)[0], 404)
        self.assertEqual(self.claim(request_id, decision=None, row_filters=other)[0], 404)
        self.assertEqual(self.rows()[0].status, "pending")

    def test_a_bad_body_is_refused(self):
        request_id = self.record(**TRIGGER)
        self.assertEqual(self.claim(request_id, decision="maybe")[0], 400)
        self.assertEqual(self.claim(request_id, note=["x"])[0], 400)
        self.assertEqual(self.rows()[0].status, "pending")


class TestExpiry(_Database):

    def test_an_inbox_request_past_its_time_expires(self):
        self.record(**TRIGGER)
        self.record(confirmation_call_id="adk-chat")  # a chat's: not expired
        later = datetime.now(timezone.utc) + timedelta(minutes=approval_service.expire_minutes() + 1)
        expired = approval_service.expire_due(now=later)
        self.assertEqual([r["session_id"] for r in expired], ["s-t"])
        trigger_row, = self.rows(session_id="s-t")
        self.assertEqual((trigger_row.status, trigger_row.decided_by, trigger_row.resume_status),
                         ("expired", "system", "resuming"))
        self.assertEqual(self.rows(session_id="s1")[0].status, "pending")
        entry, = self.rows(AuditLog)
        self.assertEqual((entry.action, entry.actor), ("approval.expired", "system"))

    def test_nothing_expires_early(self):
        self.record(**TRIGGER)
        self.assertEqual(approval_service.expire_due(), [])

    def test_expiry_resumes_with_a_rejection_in_the_background(self):
        self.record(**TRIGGER)
        later = datetime.now(timezone.utc) + timedelta(days=2)
        with patch.object(approval_service, "_now", return_value=later), \
                patch.object(approval_resume, "resume_in_background") as resume:
            approval_resume.expire_and_resume()
        resume.assert_called_once()
        self.assertEqual(resume.call_args.kwargs, {"confirmed": False})

    def test_the_wait_is_configurable(self):
        with patch.dict(os.environ, {"APPROVALS_EXPIRE_MINUTES": "30"}):
            self.record(**TRIGGER)
        row, = self.rows()
        self.assertLess(abs((row.expires_at - row.requested_at) - timedelta(minutes=30)),
                        timedelta(seconds=5))


class TestCancelWithTheAgent(_Database):

    def test_a_deleted_agents_requests_are_cancelled_once_the_deletion_commits(self):
        self.record(**TRIGGER)
        session = self.Session()
        ids = approval_service.cancel_in_transaction(session, ["refunds"])
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.rows(AuditLog), [])  # not before the commit
        session.commit()
        session.close()
        self.assertEqual(self.rows()[0].status, "cancelled")
        entry, = self.rows(AuditLog)
        self.assertEqual(entry.action, "approval.cancelled")

    def test_a_rolled_back_deletion_cancels_nothing(self):
        self.record(**TRIGGER)
        session = self.Session()
        approval_service.cancel_in_transaction(session, ["refunds"])
        session.rollback()
        session.close()
        self.assertEqual(self.rows()[0].status, "pending")
        self.assertEqual(self.rows(AuditLog), [])

    def test_delete_agent_rows_cancels_them(self):
        from shared.utils.agent_cleanup import delete_agent_rows
        self.record(**TRIGGER)
        session = self.Session()
        removed = delete_agent_rows(session, ["refunds"])
        session.commit()
        session.close()
        self.assertEqual(removed["approval_requests_cancelled"], 1)
        self.assertEqual(self.rows()[0].status, "cancelled")


class TestResume(_Database):

    def setUp(self):
        super().setUp()
        patcher = patch("shared.utils.utils.get_adk_config",
                        return_value={"adk_host": "127.0.0.1", "adk_port": 8001})
        patcher.start()
        self.addCleanup(patcher.stop)

    def claimed(self, **overrides):
        request_id = self.record(**{**TRIGGER, **overrides})
        status, outcome = approval_service.claim_decision(request_id, decision="approve",
                                                          decided_by="alice", note=None)
        self.assertEqual(status, 200)
        return outcome

    def test_the_answer_goes_to_the_paused_session(self):
        request = self.claimed()
        with patch("shared.utils.trigger_runner.collect_reply", return_value="Refund issued") as collect, \
                patch("shared.utils.trigger_runner.get_trigger_runner") as runner:
            result = approval_resume.resume(request, confirmed=True)
        host, port, payload = collect.call_args.args
        self.assertEqual((payload["app_name"], payload["user_id"], payload["session_id"]),
                         ("refunds", "trigger_runner", "s-t"))
        self.assertEqual(payload["new_message"]["parts"][0]["function_response"],
                         {"id": "adk-c1", "name": "adk_request_confirmation",
                          "response": {"confirmed": True}})
        self.assertEqual(result, {"status": "ok", "reply": "Refund issued"})
        row, = self.rows()
        self.assertEqual((row.resume_status, row.resume_result), ("ok", "Refund issued"))
        runner.return_value.deliver_after_approval.assert_called_once_with(7, "Refund issued")

    def test_a_trigger_waits_for_its_last_request(self):
        request = self.claimed()
        self.record(confirmation_call_id="adk-c2", **TRIGGER)  # still pending in the session
        with patch("shared.utils.trigger_runner.collect_reply", return_value="one done"), \
                patch("shared.utils.trigger_runner.get_trigger_runner") as runner:
            approval_resume.resume(request, confirmed=True)
        runner.return_value.deliver_after_approval.assert_not_called()

    def test_two_decisions_in_one_session_send_the_output_once(self):
        first = self.claimed()
        second = self.claimed(confirmation_call_id="adk-c2")   # decided, still being resumed
        with patch("shared.utils.trigger_runner.collect_reply", side_effect=["one done", "both done"]), \
                patch("shared.utils.trigger_runner.get_trigger_runner") as runner:
            approval_resume.resume(first, confirmed=True)
            runner.return_value.deliver_after_approval.assert_not_called()
            approval_resume.resume(second, confirmed=True)
        runner.return_value.deliver_after_approval.assert_called_once_with(7, "both done")

    def test_a_failed_resume_is_stored(self):
        request = self.claimed()
        with patch("shared.utils.trigger_runner.collect_reply", side_effect=RuntimeError("HTTP 500")), \
                patch("shared.utils.trigger_runner.get_trigger_runner") as runner:
            result = approval_resume.resume(request, confirmed=True)
        self.assertEqual(result["status"], "error")
        row, = self.rows()
        self.assertEqual((row.resume_status, row.resume_result), ("error", "HTTP 500"))
        runner.return_value.deliver_after_approval.assert_not_called()

    def test_another_sources_reply_stays_in_its_session(self):
        request = self.claimed(user_id="u1", session_id="openai_sess_1", trigger_id=None)
        with patch("shared.utils.trigger_runner.collect_reply", return_value="ok"), \
                patch("shared.utils.trigger_runner.get_trigger_runner") as runner:
            approval_resume.resume(request, confirmed=True)
        runner.return_value.deliver_after_approval.assert_not_called()


class TestTriggerWaits(_Database):

    def setUp(self):
        super().setUp()
        from shared.utils.trigger_runner import TriggerRunner
        self.runner = TriggerRunner()
        patcher = patch("shared.utils.trigger_runner._project_is_open", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trigger = MagicMock(id=7, trigger_type="cron", agent_name="refunds", project_id=5,
                                 prompt="Refund order A-7", output_type="memory_block")

    def reply(self, text, session_id):
        from shared.utils.trigger_runner import AgentReply
        reply = AgentReply(text)
        reply.session_id = session_id
        return reply

    def test_a_firing_that_paused_waits_for_the_inbox(self):
        self.record(**TRIGGER)
        self.runner._invoke_agent = MagicMock(return_value=self.reply("", "s-t"))
        self.runner._route_output = MagicMock()
        result = self.runner._execute_trigger_sync(self.trigger)
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["session_id"], "s-t")
        self.assertEqual(len(result["approval_requests"]), 1)
        self.runner._route_output.assert_not_called()

    def test_a_firing_that_did_not_pause_routes_its_output(self):
        self.runner._invoke_agent = MagicMock(return_value=self.reply("done", "s-other"))
        self.runner._route_output = MagicMock()
        self.assertEqual(self.runner._execute_trigger_sync(self.trigger)["status"], "ok")
        self.runner._route_output.assert_called_once_with(self.trigger, "done")

    def test_after_approval_the_output_goes_out_and_is_the_last_result(self):
        session = self.Session()
        trigger = AgentTrigger(name="refunds-nightly", agent_name="refunds", project_id=5,
                               prompt="go", output_type="memory_block")
        session.add(trigger)
        session.commit()
        trigger_id = trigger.id
        session.close()
        self.runner._route_output = MagicMock()
        with patch("shared.utils.database_client.get_database_client") as db:
            db.return_value.get_session.side_effect = lambda: self.Session()
            result = self.runner.deliver_after_approval(trigger_id, "Refund issued")
        self.assertEqual((result["status"], result["after_approval"]), ("ok", True))
        self.runner._route_output.assert_called_once()
        session = self.Session()
        self.assertIn("Refund issued", session.get(AgentTrigger, trigger_id).last_result)
        session.close()


class TestSurface(unittest.TestCase):
    """The /a2a/ middleware marks the run."""

    def surface_seen(self, headers):
        from shared.utils.a2a_routes import CallerAsA2AUser
        seen = {}

        async def app(scope, receive, send):
            seen["surface"] = run_surface.current()

        middleware = CallerAsA2AUser(app)
        asyncio.run(middleware({"type": "http", "path": "/a2a/refunds", "headers": headers}, None, None))
        return seen["surface"]

    def test_an_a2a_call(self):
        self.assertEqual(self.surface_seen([]), "a2a")

    def test_other_paths_are_left_alone(self):
        from shared.utils.a2a_routes import CallerAsA2AUser
        seen = {}

        async def app(scope, receive, send):
            seen["surface"] = run_surface.current()

        asyncio.run(CallerAsA2AUser(app)({"type": "http", "path": "/run_sse", "headers": []}, None, None))
        self.assertIsNone(seen["surface"])
        self.assertIsNone(run_surface.current())

    def test_the_plugin_records_the_surface(self):
        from shared.callbacks.approval_plugin import ApprovalCapturePlugin
        from google.adk.events.event import Event
        from google.genai import types
        from types import SimpleNamespace
        from shared.test.test_approval_requests import CAMEL_ARGS
        event = Event(author="refunds", content=types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(id="adk-c1", name="adk_request_confirmation", args=CAMEL_ARGS))]))
        ctx = SimpleNamespace(session=SimpleNamespace(id="ctx-1", state={}), app_name="refunds",
                              user_id="A2A_USER_ctx-1", invocation_id="e-1")
        token = run_surface.set_surface("mcp")
        try:
            with patch.object(approval_service, "record_request") as record:
                asyncio.run(ApprovalCapturePlugin().on_event_callback(invocation_context=ctx, event=event))
        finally:
            run_surface.reset(token)
        self.assertEqual(record.call_args.kwargs["source"], "mcp")


if __name__ == "__main__":
    unittest.main()
