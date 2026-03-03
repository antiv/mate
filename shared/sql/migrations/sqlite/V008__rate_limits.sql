-- Migration: Rate limits and budget config
-- Version: V008
-- Database: SQLITE

CREATE TABLE IF NOT EXISTS rate_limit_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope VARCHAR(20) NOT NULL CHECK (scope IN ('user', 'agent', 'project')),
    scope_id VARCHAR(500) NOT NULL,
    requests_per_minute INTEGER,
    tokens_per_hour INTEGER,
    tokens_per_day INTEGER,
    tokens_per_month INTEGER,
    max_tokens_per_request INTEGER,
    action_on_limit VARCHAR(20) NOT NULL DEFAULT 'block' CHECK (action_on_limit IN ('warn', 'throttle', 'block')),
    alert_thresholds TEXT,
    alert_webhook_url TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_config_scope ON rate_limit_config(scope);
CREATE INDEX IF NOT EXISTS idx_rate_limit_config_scope_id ON rate_limit_config(scope, scope_id);
