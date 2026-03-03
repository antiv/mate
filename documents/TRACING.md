# OpenTelemetry Distributed Tracing

MATE uses OpenTelemetry for distributed tracing of agent executions, LLM calls, tool invocations, RBAC checks, and memory operations.

## Setup

### Environment Variables

```env
# Enable tracing (default: false)
OTEL_TRACING_ENABLED=true

# Optional: OTLP endpoint for Jaeger, Grafana Tempo, Datadog, Honeycomb
# When unset, only DB export runs (for dashboard)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

# Service name for traces
OTEL_SERVICE_NAME=mate

# Store spans in database for dashboard viewer (default: true when tracing enabled)
OTEL_TRACES_DB_EXPORT=true
```

### Zero Performance When Disabled

When `OTEL_TRACING_ENABLED=false` (default), tracing is completely disabled. No overhead is incurred.

## Instrumentation

| Instrumentation Point | Span Name | Attributes |
|----------------------|-----------|------------|
| LLM inference | `gen_ai.inference` | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| RBAC check | `mate.rbac_check` | `mate.agent.name`, `mate.user.id`, `mate.rbac.allowed` |
| Tool invocation | `mate.tool` | `mate.tool.name`, `mate.agent.name` |
| Session memory | `mate.memory` | `mate.memory.operation` (add/search) |
| Memory blocks | `mate.memory_blocks` | `mate.memory.operation` |

## Dashboard

The `/dashboard/traces` page shows traces from the database when `OTEL_TRACES_DB_EXPORT=true`. Traces are grouped by trace ID with a call graph showing span hierarchy and latency.

## OTLP Export

To export to Jaeger, Grafana Tempo, Datadog, or Honeycomb:

1. Run an OTLP-compatible collector (e.g. Jaeger all-in-one, Grafana OTLP collector)
2. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to the collector URL
3. Default: `http://localhost:4318` (OTLP HTTP)

## W3C Trace Context

Trace context is propagated via `traceparent` and `tracestate` headers. The auth server proxy forwards requests to ADK with trace context injected when tracing is enabled.
