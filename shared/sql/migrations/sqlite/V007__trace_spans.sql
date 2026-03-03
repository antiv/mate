-- Migration: Create trace_spans table for OpenTelemetry distributed tracing
-- Version: V007
-- Database: SQLITE

CREATE TABLE IF NOT EXISTS trace_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32) NOT NULL,
    parent_span_id VARCHAR(32),
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(32),
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    duration_ms INTEGER,
    attributes TEXT,
    status VARCHAR(32),
    error_message TEXT,
    UNIQUE(trace_id, span_id)
);

CREATE INDEX IF NOT EXISTS idx_trace_spans_trace_id ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_trace_spans_start_time ON trace_spans(start_time);
