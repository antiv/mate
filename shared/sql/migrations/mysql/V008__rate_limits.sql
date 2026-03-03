-- Migration: Rate limits and budget config
-- Version: V008
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS rate_limit_config (
    id INT AUTO_INCREMENT PRIMARY KEY,
    scope VARCHAR(20) NOT NULL,
    scope_id VARCHAR(500) NOT NULL,
    requests_per_minute INT,
    tokens_per_hour INT,
    tokens_per_day INT,
    tokens_per_month INT,
    max_tokens_per_request INT,
    action_on_limit VARCHAR(20) NOT NULL DEFAULT 'block',
    alert_thresholds TEXT,
    alert_webhook_url TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_scope_scope_id (scope, scope_id),
    INDEX idx_rate_limit_config_scope (scope),
    INDEX idx_rate_limit_config_scope_id (scope, scope_id),
    CONSTRAINT chk_scope CHECK (scope IN ('user', 'agent', 'project')),
    CONSTRAINT chk_action CHECK (action_on_limit IN ('warn', 'throttle', 'block'))
);
