-- Migration: Trigger Engine
-- Version: V012
-- Database: SQLite

CREATE TABLE IF NOT EXISTS agent_triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    trigger_type    VARCHAR(50)  NOT NULL DEFAULT 'cron',
    agent_name      VARCHAR(255) NOT NULL,
    project_id      INTEGER      NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    prompt          TEXT         NOT NULL DEFAULT '',
    cron_expression VARCHAR(100),
    webhook_path    VARCHAR(255) UNIQUE,
    fire_key_hash   VARCHAR(255),
    output_type     VARCHAR(50)  NOT NULL DEFAULT 'memory_block',
    output_config   TEXT,
    is_enabled      INTEGER      NOT NULL DEFAULT 1,
    last_fired_at   DATETIME,
    last_result     TEXT,
    created_by      VARCHAR(255),
    created_at      DATETIME     NOT NULL DEFAULT (datetime('now')),
    updated_at      DATETIME     NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_at_agent_name   ON agent_triggers(agent_name);
CREATE INDEX IF NOT EXISTS idx_at_project_id   ON agent_triggers(project_id);
CREATE INDEX IF NOT EXISTS idx_at_trigger_type ON agent_triggers(trigger_type);
CREATE INDEX IF NOT EXISTS idx_at_is_enabled   ON agent_triggers(is_enabled);
