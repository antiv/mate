"""
OpenTelemetry tracer for MATE.

Uses ADK's TracerProvider when available (adds our DB exporter to it).
Creates our own provider only when ADK has not set one.
Lazy init; no overhead when OTEL_TRACING_ENABLED=false.
"""

import logging
import os
from contextlib import contextmanager
from typing import Optional, Generator

logger = logging.getLogger(__name__)

_provider_initialized = False
_db_exporter_added = False


def _init_tracer_provider():
    """Add our DB exporter to ADK's TracerProvider when available (avoids override warning).
    If no SDK provider exists, create our own."""
    global _provider_initialized, _db_exporter_added
    from .tracing_config import (
        is_tracing_enabled,
        get_service_name,
        is_db_export_enabled,
    )
    from opentelemetry import trace

    if not is_tracing_enabled():
        return

    if _provider_initialized:
        return

    _provider_initialized = True

    try:
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        current = trace.get_tracer_provider()

        # If ADK already set an SDK TracerProvider, add our exporter to it (no override)
        if isinstance(current, SDKTracerProvider) and is_db_export_enabled() and not _db_exporter_added:
            try:
                from .db_span_exporter import DatabaseSpanExporter
                current.add_span_processor(
                    BatchSpanProcessor(DatabaseSpanExporter(), schedule_delay_millis=1000)
                )
                _db_exporter_added = True
                logger.info("Tracing: Added DB exporter to ADK TracerProvider (trace_spans table)")
            except Exception as e:
                logger.warning("Database span exporter not available: %s", e)
            return

        # No SDK provider yet - create our own
        from opentelemetry.sdk.resources import Resource

        provider = SDKTracerProvider(resource=Resource.create({"service.name": get_service_name()}))

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            except Exception as e:
                logger.warning("OTLP exporter not available: %s", e)

        if is_db_export_enabled():
            try:
                from .db_span_exporter import DatabaseSpanExporter
                provider.add_span_processor(
                    BatchSpanProcessor(DatabaseSpanExporter(), schedule_delay_millis=1000)
                )
                _db_exporter_added = True
                logger.info("Tracing: TracerProvider initialized with DB exporter (trace_spans table)")
            except Exception as e:
                logger.warning("Database span exporter not available: %s", e)
        else:
            logger.info("Tracing: TracerProvider initialized without DB exporter")

        trace.set_tracer_provider(provider)
    except Exception as e:
        logger.warning("Failed to initialize tracing: %s", e)


def get_tracer(name: str = "mate", version: str = "1.0.0"):
    """Get a tracer instance. Lazy-initializes the provider on first call."""
    _init_tracer_provider()
    from opentelemetry import trace
    return trace.get_tracer(name, version)


@contextmanager
def trace_context(span_name: str, attributes: Optional[dict] = None) -> Generator:
    """Context manager for creating a span. Use when tracing is enabled."""
    tracer = get_tracer()
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            for k, v in attributes.items():
                if v is not None:
                    span.set_attribute(str(k), v)
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            from opentelemetry import trace as trace_api
            span.set_status(trace_api.Status(trace_api.StatusCode.ERROR, str(e)))
            raise
