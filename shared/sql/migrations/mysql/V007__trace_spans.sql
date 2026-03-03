-- Migration: Create trace_spans table for OpenTelemetry distributed tracing
-- Version: V007
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS trace_spans (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32) NOT NULL,
    parent_span_id VARCHAR(32),
    name VARCHAR(255) NOT NULL,
    kind VARCHAR(32),
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    duration_ms INT,
    attributes JSON,
    status VARCHAR(32),
    error_message TEXT,
    UNIQUE KEY uk_trace_span (trace_id, span_id),
    INDEX idx_trace_spans_trace_id (trace_id),
    INDEX idx_trace_spans_start_time (start_time)
);
