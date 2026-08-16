#!/usr/bin/env python3
"""
Unit tests for per-response metrics.

Latency is recorded at the runtime's invocation boundary because only two of the
eight surfaces that invoke an agent pass through the auth server's proxy. If the
plugin stops being registered, or origin classification drifts, the usage page
quietly reports percentiles over the wrong population.
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import AgentResponse, Base
from shared.utils.response_metrics import finish_response, resolve_origin, start_response


class TestResolveOrigin(unittest.TestCase):

    def test_background_callers_are_not_chat(self):
        # A trigger that runs for two minutes must not define the p95 a customer reads
        self.assertEqual(resolve_origin("trigger_runner", None), "trigger")
        self.assertEqual(resolve_origin("eval_runner", None), "eval")

    def test_session_prefixes(self):
        self.assertEqual(resolve_origin("u1", "openai_sess_abc"), "api")
        self.assertEqual(resolve_origin("u1", "slack_T123_C456"), "slack")

    def test_defaults_to_chat(self):
        self.assertEqual(resolve_origin("u1", "some-session"), "chat")
        self.assertEqual(resolve_origin(None, None), "chat")

    def test_user_id_wins_over_session_prefix(self):
        # A trigger firing an agent that happens to use a slack session is still a trigger
        self.assertEqual(resolve_origin("trigger_runner", "slack_T_C"), "trigger")


class TestRecordResponse(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db_client = MagicMock()
        self.db_client.is_connected.return_value = True
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.patcher = patch("shared.utils.response_metrics.get_database_client",
                             return_value=self.db_client)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _row(self, invocation_id):
        session = self.Session()
        try:
            return session.query(AgentResponse).filter(
                AgentResponse.invocation_id == invocation_id).one()
        finally:
            session.close()

    def test_open_then_close(self):
        self.assertTrue(start_response(invocation_id="inv-1", session_id="openai_sess_x",
                                       user_id="u1", agent_name="a1"))
        opened = self._row("inv-1")
        self.assertEqual(opened.status, "RUNNING")
        self.assertIsNone(opened.duration_ms)
        self.assertEqual(opened.origin, "api")

        self.assertTrue(finish_response(invocation_id="inv-1", duration_ms=1234))
        closed = self._row("inv-1")
        self.assertEqual(closed.status, "SUCCESS")
        self.assertEqual(closed.duration_ms, 1234)

    def test_unfinished_invocation_stays_visible(self):
        # ADK skips its after-run hook when an invocation raises, so a failed response
        # leaves the row RUNNING instead of disappearing entirely
        start_response(invocation_id="inv-hung", agent_name="a1")
        row = self._row("inv-hung")
        self.assertEqual(row.status, "RUNNING")
        self.assertIsNone(row.duration_ms)

    def test_closing_twice_is_a_no_op(self):
        start_response(invocation_id="inv-2", agent_name="a1")
        self.assertTrue(finish_response(invocation_id="inv-2", duration_ms=10))
        self.assertFalse(finish_response(invocation_id="inv-2", duration_ms=99))
        self.assertEqual(self._row("inv-2").duration_ms, 10)

    def test_closing_an_unknown_invocation_is_a_no_op(self):
        self.assertFalse(finish_response(invocation_id="never-opened", duration_ms=5))

    def test_error_status_is_recorded(self):
        start_response(invocation_id="inv-3", agent_name="a1")
        finish_response(invocation_id="inv-3", duration_ms=42, status="ERROR")
        row = self._row("inv-3")
        self.assertEqual(row.status, "ERROR")
        self.assertEqual(row.duration_ms, 42)

    def test_database_failure_is_swallowed(self):
        # This runs beside a live response — it must never surface to the caller
        self.db_client.get_session.side_effect = RuntimeError("db down")
        self.assertFalse(start_response(invocation_id="inv-4"))
        self.assertFalse(finish_response(invocation_id="inv-4", duration_ms=1))

    def test_no_database_returns_false(self):
        self.db_client.is_connected.return_value = False
        self.assertFalse(start_response(invocation_id="inv-5"))
        self.assertFalse(finish_response(invocation_id="inv-5", duration_ms=1))


class TestMetricsPlugin(unittest.TestCase):

    def _context(self, invocation_id="inv-9"):
        return SimpleNamespace(
            invocation_id=invocation_id,
            user_id="u1",
            session=SimpleNamespace(id="s1"),
            agent=SimpleNamespace(name="a1"),
        )

    def test_times_the_whole_invocation(self):
        from shared.callbacks.metrics_plugin import MetricsPlugin
        plugin = MetricsPlugin()
        ctx = self._context()
        with patch("shared.callbacks.metrics_plugin.start_response") as opened, \
                patch("shared.callbacks.metrics_plugin.finish_response") as closed:
            asyncio.run(plugin.before_run_callback(invocation_context=ctx))
            asyncio.run(plugin.after_run_callback(invocation_context=ctx))
        open_kwargs = opened.call_args.kwargs
        self.assertEqual(open_kwargs["invocation_id"], "inv-9")
        self.assertEqual(open_kwargs["agent_name"], "a1")
        self.assertEqual(open_kwargs["session_id"], "s1")
        close_kwargs = closed.call_args.kwargs
        self.assertEqual(close_kwargs["invocation_id"], "inv-9")
        self.assertGreaterEqual(close_kwargs["duration_ms"], 0)

    def test_after_without_before_records_nothing(self):
        from shared.callbacks.metrics_plugin import MetricsPlugin
        plugin = MetricsPlugin()
        with patch("shared.callbacks.metrics_plugin.finish_response") as record:
            asyncio.run(plugin.after_run_callback(invocation_context=self._context()))
        record.assert_not_called()

    def test_start_state_does_not_leak(self):
        from shared.callbacks.metrics_plugin import MetricsPlugin
        plugin = MetricsPlugin()
        ctx = self._context()
        with patch("shared.callbacks.metrics_plugin.start_response"), \
                patch("shared.callbacks.metrics_plugin.finish_response"):
            asyncio.run(plugin.before_run_callback(invocation_context=ctx))
            asyncio.run(plugin.after_run_callback(invocation_context=ctx))
        self.assertEqual(plugin._started, {})

    def test_recording_failure_does_not_raise(self):
        from shared.callbacks.metrics_plugin import MetricsPlugin
        plugin = MetricsPlugin()
        ctx = self._context()
        with patch("shared.callbacks.metrics_plugin.start_response",
                   side_effect=RuntimeError("boom")), \
                patch("shared.callbacks.metrics_plugin.finish_response",
                      side_effect=RuntimeError("boom")):
            asyncio.run(plugin.before_run_callback(invocation_context=ctx))
            asyncio.run(plugin.after_run_callback(invocation_context=ctx))

    def test_implements_no_model_callbacks(self):
        # It is registered alongside the per-agent model callbacks, so overriding any
        # of those here would double-execute them
        from shared.callbacks.metrics_plugin import MetricsPlugin
        from google.adk.plugins.base_plugin import BasePlugin
        for hook in ("before_model_callback", "after_model_callback",
                     "on_model_error_callback", "before_tool_callback"):
            self.assertIs(getattr(MetricsPlugin, hook), getattr(BasePlugin, hook), hook)


class TestPluginRegistration(unittest.TestCase):

    def test_metrics_plugin_registered_without_mate_plugins_enabled(self):
        # The whole point is that latency does not depend on that flag
        import inspect
        from shared.utils import utils
        source = inspect.getsource(utils)
        metrics_at = source.index("MetricsPlugin()")
        gate_at = source.index('MATE_PLUGINS_ENABLED", "false"')
        self.assertLess(metrics_at, gate_at,
                        "MetricsPlugin must be appended before the MATE_PLUGINS_ENABLED gate")


class TestInvocationIdGrouping(unittest.TestCase):

    def test_token_rows_use_the_invocation_id(self):
        # Without this, one response's several model calls cannot be grouped at all
        from shared.callbacks.token_usage_callback import _get_invocation_id
        ctx = SimpleNamespace()
        ctx._invocation_context = SimpleNamespace(invocation_id="e-42")
        self.assertEqual(_get_invocation_id(ctx), "e-42")

    def test_missing_invocation_context_is_tolerated(self):
        from shared.callbacks.token_usage_callback import _get_invocation_id
        self.assertIsNone(_get_invocation_id(SimpleNamespace()))
        self.assertIsNone(_get_invocation_id(object()))


if __name__ == '__main__':
    unittest.main()
