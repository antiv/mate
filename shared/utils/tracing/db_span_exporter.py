"""
Custom OpenTelemetry SpanExporter that writes spans to the database.

Used for the dashboard trace viewer when OTEL_TRACES_DB_EXPORT=true.
"""

import json
import logging
from typing import Sequence
from datetime import datetime, timezone

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)


def _serialize_attributes(span: ReadableSpan) -> dict:
    """Serialize span attributes to JSON-serializable dict."""
    attrs = {}
    if span.attributes:
        for key, value in span.attributes.items():
            if value is not None:
                attrs[key] = str(value) if not isinstance(value, (str, int, float, bool)) else value
    return attrs


class DatabaseSpanExporter(SpanExporter):
    """Exports spans to the trace_spans table for dashboard viewing."""

    def __init__(self):
        """Initialize the database exporter."""
        self._db_client = None

    def _get_db_client(self):
        """Lazy load database client."""
        if self._db_client is None:
            try:
                from shared.utils.database_client import get_database_client
                self._db_client = get_database_client()
            except Exception as e:
                logger.warning("Could not get database client for trace export: %s", e)
        return self._db_client

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export spans to the database."""
        db = self._get_db_client()
        connected = bool(db and db.is_connected())
        logger.debug(
            "Tracing: DB export n_spans=%s db_connected=%s",
            len(spans), connected,
        )
        if not db or not connected:
            return SpanExportResult.FAILURE

        session = db.get_session()
        if not session:
            logger.warning("Tracing: get_session() returned None")
            return SpanExportResult.FAILURE

        try:
            from sqlalchemy import text
            for span in spans:
                try:
                    start_time = datetime.fromtimestamp(
                        span.start_time / 1e9, tz=timezone.utc
                    )
                    end_time = None
                    duration_ms = None
                    if span.end_time:
                        end_time = datetime.fromtimestamp(
                            span.end_time / 1e9, tz=timezone.utc
                        )
                        duration_ms = int((span.end_time - span.start_time) / 1e6)

                    status = "UNSET"
                    status_code = getattr(span.status, "status_code", None)
                    if status_code is not None:
                        status = str(status_code).split(".")[-1] if hasattr(status_code, "name") else str(status_code)

                    error_message = None
                    if span.status and getattr(span.status, "description", None):
                        error_message = span.status.description

                    parent_span_id = None
                    if span.parent and hasattr(span.parent, "span_id"):
                        parent_span_id = format(span.parent.span_id, "016x")

                    attrs = _serialize_attributes(span)
                    attributes_json = json.dumps(attrs) if attrs else "{}"

                    trace_id = format(span.context.trace_id, "032x")
                    span_id = format(span.context.span_id, "016x")
                    name = (span.name[:255] if span.name else "span")
                    logger.debug("Tracing: inserting span name=%s trace_id=%s", name, trace_id)
                    kind = span.kind.name if span.kind else "INTERNAL"

                    # Delete existing then insert (works across PostgreSQL, SQLite, MySQL)
                    session.execute(
                        text("DELETE FROM trace_spans WHERE trace_id = :tid AND span_id = :sid"),
                        {"tid": trace_id, "sid": span_id},
                    )
                    session.execute(
                        text("""
                            INSERT INTO trace_spans (trace_id, span_id, parent_span_id, name, kind, start_time, end_time, duration_ms, attributes, status, error_message)
                            VALUES (:trace_id, :span_id, :parent_span_id, :name, :kind, :start_time, :end_time, :duration_ms, :attributes, :status, :error_message)
                        """),
                        {
                            "trace_id": trace_id,
                            "span_id": span_id,
                            "parent_span_id": parent_span_id,
                            "name": name,
                            "kind": kind,
                            "start_time": start_time,
                            "end_time": end_time,
                            "duration_ms": duration_ms,
                            "attributes": attributes_json,
                            "status": status,
                            "error_message": error_message,
                        },
                    )
                except Exception as e:
                    logger.warning("Failed to export span %s: %s", span.name, e)
            session.commit()
            logger.debug("Tracing: committed %s span(s) to trace_spans", len(spans))
            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.warning("Database span export failed: %s", e)
            try:
                session.rollback()
            except Exception:
                pass
            return SpanExportResult.FAILURE
        finally:
            try:
                session.close()
            except Exception:
                pass

    def shutdown(self) -> None:
        """Shutdown the exporter."""
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush pending spans."""
        return True
