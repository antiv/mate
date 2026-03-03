#!/usr/bin/env python3
"""
Unit tests for tracing: config, DB exporter, and tracer initialization.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTracingConfig(unittest.TestCase):
    def setUp(self):
        import shared.utils.tracing.tracing_config as cfg
        cfg._TRACING_ENABLED = None

    def tearDown(self):
        import shared.utils.tracing.tracing_config as cfg
        cfg._TRACING_ENABLED = None

    def test_is_tracing_enabled_default_false(self):
        with patch.dict(os.environ, {}, clear=False):
            if "OTEL_TRACING_ENABLED" in os.environ:
                del os.environ["OTEL_TRACING_ENABLED"]
            from shared.utils.tracing.tracing_config import is_tracing_enabled
            self.assertFalse(is_tracing_enabled())

    def test_is_tracing_enabled_true_when_set(self):
        with patch.dict(os.environ, {"OTEL_TRACING_ENABLED": "true"}):
            from shared.utils.tracing.tracing_config import is_tracing_enabled
            self.assertTrue(is_tracing_enabled())

    def test_is_tracing_enabled_1_yes(self):
        with patch.dict(os.environ, {"OTEL_TRACING_ENABLED": "1"}):
            from shared.utils.tracing.tracing_config import is_tracing_enabled
            self.assertTrue(is_tracing_enabled())

    def test_is_db_export_enabled_false_when_tracing_disabled(self):
        with patch.dict(os.environ, {"OTEL_TRACING_ENABLED": "false", "OTEL_TRACES_DB_EXPORT": "true"}):
            import shared.utils.tracing.tracing_config as cfg
            cfg._TRACING_ENABLED = False
            from shared.utils.tracing.tracing_config import is_db_export_enabled
            self.assertFalse(is_db_export_enabled())

    def test_is_db_export_enabled_true_when_both_set(self):
        with patch.dict(os.environ, {"OTEL_TRACING_ENABLED": "true", "OTEL_TRACES_DB_EXPORT": "true"}):
            import shared.utils.tracing.tracing_config as cfg
            cfg._TRACING_ENABLED = True
            from shared.utils.tracing.tracing_config import is_db_export_enabled
            self.assertTrue(is_db_export_enabled())


class TestDatabaseSpanExporter(unittest.TestCase):
    """Test that DatabaseSpanExporter.export() writes to DB and returns SUCCESS."""

    def _make_mock_span(
        self,
        name="test.span",
        trace_id=0x0123456789ABCDEF0123456789ABCDEF,
        span_id=0x0123456789ABCDEF,
        start_time=1_000_000_000,
        end_time=1_500_000_000,
        parent=None,
    ):
        from opentelemetry.trace import SpanContext, TraceFlags
        ctx = SpanContext(trace_id=trace_id, span_id=span_id, is_remote=False, trace_flags=TraceFlags(0))
        span = MagicMock()
        span.name = name
        span.context = ctx
        span.start_time = start_time
        span.end_time = end_time
        span.parent = parent
        span.status = MagicMock(status_code=None, description=None)
        span.attributes = {}
        span.kind = MagicMock()
        span.kind.name = "INTERNAL"
        return span

    @patch("shared.utils.tracing.db_span_exporter.DatabaseSpanExporter._get_db_client")
    def test_export_returns_failure_when_db_not_connected(self, mock_get_client):
        mock_get_client.return_value = None
        from shared.utils.tracing.db_span_exporter import DatabaseSpanExporter
        from opentelemetry.sdk.trace.export import SpanExportResult
        exporter = DatabaseSpanExporter()
        span = self._make_mock_span()
        result = exporter.export([span])
        self.assertEqual(result, SpanExportResult.FAILURE)

    @patch("shared.utils.tracing.db_span_exporter.DatabaseSpanExporter._get_db_client")
    def test_export_returns_failure_when_session_none(self, mock_get_client):
        mock_db = MagicMock()
        mock_db.is_connected.return_value = True
        mock_db.get_session.return_value = None
        mock_get_client.return_value = mock_db
        from shared.utils.tracing.db_span_exporter import DatabaseSpanExporter
        from opentelemetry.sdk.trace.export import SpanExportResult
        exporter = DatabaseSpanExporter()
        span = self._make_mock_span()
        result = exporter.export([span])
        self.assertEqual(result, SpanExportResult.FAILURE)

    @patch("shared.utils.tracing.db_span_exporter.DatabaseSpanExporter._get_db_client")
    def test_export_calls_session_execute_and_commit(self, mock_get_client):
        mock_session = MagicMock()
        mock_db = MagicMock()
        mock_db.is_connected.return_value = True
        mock_db.get_session.return_value = mock_session
        mock_get_client.return_value = mock_db
        from shared.utils.tracing.db_span_exporter import DatabaseSpanExporter
        from opentelemetry.sdk.trace.export import SpanExportResult
        exporter = DatabaseSpanExporter()
        span = self._make_mock_span(name="gen_ai.inference")
        result = exporter.export([span])
        self.assertEqual(result, SpanExportResult.SUCCESS)
        self.assertGreaterEqual(mock_session.execute.call_count, 2)
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()


class TestTracerInitialization(unittest.TestCase):
    def setUp(self):
        import shared.utils.tracing.tracer as tracer_mod
        tracer_mod._provider_initialized = False
        import shared.utils.tracing.tracing_config as cfg
        cfg._TRACING_ENABLED = None

    def tearDown(self):
        import shared.utils.tracing.tracer as tracer_mod
        tracer_mod._provider_initialized = False

    @patch.dict(os.environ, {"OTEL_TRACING_ENABLED": "true", "OTEL_TRACES_DB_EXPORT": "true"})
    @patch("opentelemetry.trace.set_tracer_provider")
    def test_get_tracer_sets_provider_when_tracing_enabled(self, mock_set_provider):
        import shared.utils.tracing.tracing_config as cfg
        cfg._TRACING_ENABLED = True
        from shared.utils.tracing.tracer import get_tracer
        get_tracer("mate", "1.0.0")
        mock_set_provider.assert_called_once()

    def test_span_lifecycle_exported_to_db(self):
        """Create span, end it, force flush; verify exporter.export() is invoked."""
        with patch.dict(
            os.environ,
            {"OTEL_TRACING_ENABLED": "true", "OTEL_TRACES_DB_EXPORT": "true"},
            clear=False,
        ):
            from shared.utils.tracing.db_span_exporter import DatabaseSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry import trace

            exporter = DatabaseSpanExporter()
            with patch.object(exporter, "_get_db_client") as mock_get:
                mock_session = MagicMock()
                mock_db = MagicMock()
                mock_db.is_connected.return_value = True
                mock_db.get_session.return_value = mock_session
                mock_get.return_value = mock_db
                provider = TracerProvider(resource=Resource.create({"service.name": "mate-test"}))
                provider.add_span_processor(BatchSpanProcessor(exporter))
                trace.set_tracer_provider(provider)
                tracer = trace.get_tracer("mate", "1.0.0")
                span = tracer.start_span("test.span")
                span.end()
                for proc in provider._active_span_processor._span_processors:
                    if hasattr(proc, "force_flush"):
                        proc.force_flush(timeout_millis=5000)
                mock_session.commit.assert_called()
                mock_session.execute.assert_called()
