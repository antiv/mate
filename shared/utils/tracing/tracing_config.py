"""
OpenTelemetry tracing configuration for MATE.

Feature flag OTEL_TRACING_ENABLED controls whether tracing is active.
When disabled, uses NoOpSpanProcessor for zero overhead.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_TRACING_ENABLED: Optional[bool] = None


def is_tracing_enabled() -> bool:
    """Check if distributed tracing is enabled."""
    global _TRACING_ENABLED
    if _TRACING_ENABLED is None:
        val = os.getenv("OTEL_TRACING_ENABLED", "false").lower()
        _TRACING_ENABLED = val in ("true", "1", "yes")
    return _TRACING_ENABLED


def get_otlp_endpoint() -> str:
    """Get OTLP exporter endpoint."""
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")


def get_otlp_protocol() -> str:
    """Get OTLP protocol (http/protobuf or grpc)."""
    return os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")


def get_service_name() -> str:
    """Get service name for traces."""
    return os.getenv("OTEL_SERVICE_NAME", "mate")


def is_db_export_enabled() -> bool:
    """Check if spans should be stored in DB for dashboard."""
    if not is_tracing_enabled():
        return False
    val = os.getenv("OTEL_TRACES_DB_EXPORT", "true").lower()
    return val in ("true", "1", "yes")
