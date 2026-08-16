-- Migration: Alert Rules
-- Version: V026
-- Database: SQLite

CREATE TABLE IF NOT EXISTS alert_rules (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               VARCHAR(255) NOT NULL,
    description        TEXT,
    scope              VARCHAR(20)  NOT NULL CHECK (scope IN ('user', 'agent', 'project', 'global')),
    scope_id           VARCHAR(255),
    condition_type     VARCHAR(50)  NOT NULL CHECK (condition_type IN ('agent_error_count', 'budget_threshold', 'guardrail_count')),
    condition_config   TEXT,
    destination_type   VARCHAR(50)  NOT NULL CHECK (destination_type IN ('http', 'email')),
    destination_config TEXT,
    cooldown_seconds   INTEGER      NOT NULL DEFAULT 3600,
    is_enabled         INTEGER      NOT NULL DEFAULT 1,
    last_fired_at      DATETIME,
    last_state         TEXT,
    last_error         TEXT,
    fire_count         INTEGER      NOT NULL DEFAULT 0,
    created_by         VARCHAR(255),
    created_at         DATETIME     NOT NULL DEFAULT (datetime('now')),
    updated_at         DATETIME     NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ar_is_enabled     ON alert_rules(is_enabled);
CREATE INDEX IF NOT EXISTS idx_ar_scope          ON alert_rules(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_ar_condition_type ON alert_rules(condition_type);

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
    json_object('url', rlc.alert_webhook_url),
    3600,
    1,
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
