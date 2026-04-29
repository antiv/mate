-- Migration: Trigger Engine
-- Version: V012
-- Database: PostgreSQL

CREATE TABLE IF NOT EXISTS agent_triggers (
    id              SERIAL PRIMARY KEY,
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
    is_enabled      BOOLEAN      NOT NULL DEFAULT TRUE,
    last_fired_at   TIMESTAMP WITH TIME ZONE,
    last_result     TEXT,
    created_by      VARCHAR(255),
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_at_agent_name   ON agent_triggers(agent_name);
CREATE INDEX IF NOT EXISTS idx_at_project_id   ON agent_triggers(project_id);
CREATE INDEX IF NOT EXISTS idx_at_trigger_type ON agent_triggers(trigger_type);
CREATE INDEX IF NOT EXISTS idx_at_is_enabled   ON agent_triggers(is_enabled);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_agent_triggers_updated_at'
    ) THEN
        CREATE TRIGGER update_agent_triggers_updated_at
            BEFORE UPDATE ON agent_triggers
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
