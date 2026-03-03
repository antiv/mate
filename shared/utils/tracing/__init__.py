"""
OpenTelemetry distributed tracing for MATE.

Provides structured spans for agent turns, LLM calls, tool invocations,
RBAC checks, and memory operations. Export to OTLP-compatible backends
(Jaeger, Grafana Tempo, Datadog, Honeycomb) and optional DB storage for dashboard.
"""

from .tracer import get_tracer, trace_context
from .genai_attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_RESPONSE_MODEL,
)

__all__ = [
    "get_tracer",
    "trace_context",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "GEN_AI_RESPONSE_MODEL",
]
