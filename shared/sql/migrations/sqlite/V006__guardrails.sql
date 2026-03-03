-- Migration: Add guardrail_config to agents_config and create guardrail_logs table
-- Version: V006
-- Database: SQLITE

-- Add guardrail_config column to agents_config
ALTER TABLE agents_config ADD COLUMN guardrail_config TEXT;

-- Create guardrail_logs table for tracking guardrail triggers
CREATE TABLE IF NOT EXISTS guardrail_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255),
    user_id VARCHAR(255),
    agent_name VARCHAR(255),
    guardrail_type VARCHAR(100) NOT NULL,
    phase VARCHAR(20) NOT NULL DEFAULT 'input',
    action_taken VARCHAR(50) NOT NULL,
    matched_content TEXT,
    details TEXT,
    timestamp DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gl_agent_name ON guardrail_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_gl_guardrail_type ON guardrail_logs(guardrail_type);
CREATE INDEX IF NOT EXISTS idx_gl_action_taken ON guardrail_logs(action_taken);
CREATE INDEX IF NOT EXISTS idx_gl_timestamp ON guardrail_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_gl_user_id ON guardrail_logs(user_id);
