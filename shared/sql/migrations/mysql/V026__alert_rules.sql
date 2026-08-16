-- Migration: Alert Rules
-- Version: V026
-- Database: MySQL

CREATE TABLE IF NOT EXISTS alert_rules (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    name               VARCHAR(255) NOT NULL,
    description        TEXT,
    scope              VARCHAR(20)  NOT NULL,
    scope_id           VARCHAR(255),
    condition_type     VARCHAR(50)  NOT NULL,
    condition_config   TEXT,
    destination_type   VARCHAR(50)  NOT NULL,
    destination_config TEXT,
    cooldown_seconds   INT          NOT NULL DEFAULT 3600,
    is_enabled         TINYINT(1)   NOT NULL DEFAULT 1,
    last_fired_at      DATETIME,
    last_state         TEXT,
    last_error         TEXT,
    fire_count         INT          NOT NULL DEFAULT 0,
    created_by         VARCHAR(255),
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_ar_is_enabled     (is_enabled),
    INDEX idx_ar_scope          (scope, scope_id),
    INDEX idx_ar_condition_type (condition_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Carry over budget alerts configured on rate_limit_config. The callback that used
-- to send them is removed in this release, so without this backfill anyone who had
-- set alert_webhook_url would silently stop being notified.
INSERT INTO alert_rules (name, description, scope, scope_id, condition_type,
                         condition_config, destination_type, destination_config,
                         cooldown_seconds, is_enabled, created_by)
SELECT
    CONCAT('Budget alert: ', rlc.scope, ' ', rlc.scope_id),
    'Migrated from rate_limit_config.alert_webhook_url',
    rlc.scope,
    rlc.scope_id,
    'budget_threshold',
    '{"period": "day", "threshold_pct": 90}',
    'http',
    JSON_OBJECT('url', rlc.alert_webhook_url),
    3600,
    1,
    'migration_v026'
FROM rate_limit_config rlc
WHERE rlc.alert_webhook_url IS NOT NULL
  AND rlc.alert_webhook_url <> ''
  AND rlc.tokens_per_day IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM (SELECT * FROM alert_rules) ar
      WHERE ar.scope = rlc.scope AND ar.scope_id = rlc.scope_id
        AND ar.condition_type = 'budget_threshold'
  );
