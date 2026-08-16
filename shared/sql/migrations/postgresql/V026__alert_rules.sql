-- Migration: Alert Rules
-- Version: V026
-- Database: PostgreSQL

CREATE TABLE IF NOT EXISTS alert_rules (
    id                 SERIAL PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    description        TEXT,
    scope              VARCHAR(20)  NOT NULL CHECK (scope IN ('user', 'agent', 'project', 'global')),
    scope_id           VARCHAR(255),
    condition_type     VARCHAR(50)  NOT NULL CHECK (condition_type IN ('agent_error_count', 'budget_threshold', 'guardrail_count')),
    condition_config   TEXT,
    destination_type   VARCHAR(50)  NOT NULL CHECK (destination_type IN ('http', 'email')),
    destination_config TEXT,
    cooldown_seconds   INTEGER      NOT NULL DEFAULT 3600,
    is_enabled         BOOLEAN      NOT NULL DEFAULT TRUE,
    last_fired_at      TIMESTAMP WITH TIME ZONE,
    last_state         TEXT,
    last_error         TEXT,
    fire_count         INTEGER      NOT NULL DEFAULT 0,
    created_by         VARCHAR(255),
    created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ar_is_enabled     ON alert_rules(is_enabled);
CREATE INDEX IF NOT EXISTS idx_ar_scope          ON alert_rules(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_ar_condition_type ON alert_rules(condition_type);

-- No migration ever creates update_updated_at_column(); V012 only references it, and
-- the whole file is executed as a single statement, so a missing function rolls the
-- table back with it. Define it here so this migration stands on its own.
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $func$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$func$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_alert_rules_updated_at'
    ) THEN
        CREATE TRIGGER update_alert_rules_updated_at
            BEFORE UPDATE ON alert_rules
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- Carry over budget alerts configured on rate_limit_config. The callback that used
-- to send them is removed in this release, so without this backfill anyone who had
-- set alert_webhook_url would silently stop being notified.
INSERT INTO alert_rules (name, description, scope, scope_id, condition_type,
                         condition_config, destination_type, destination_config,
                         cooldown_seconds, is_enabled, created_by)
SELECT
    'Budget alert: ' || rlc.scope || ' ' || rlc.scope_id,
    'Migrated from rate_limit_config.alert_webhook_url',
    rlc.scope,
    rlc.scope_id,
    'budget_threshold',
    '{"period": "day", "threshold_pct": 90}',
    'http',
    json_build_object('url', rlc.alert_webhook_url)::text,
    3600,
    TRUE,
    'migration_v026'
FROM rate_limit_config rlc
WHERE rlc.alert_webhook_url IS NOT NULL
  AND rlc.alert_webhook_url <> ''
  AND rlc.tokens_per_day IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM alert_rules ar
      WHERE ar.scope = rlc.scope AND ar.scope_id = rlc.scope_id
        AND ar.condition_type = 'budget_threshold'
  );
