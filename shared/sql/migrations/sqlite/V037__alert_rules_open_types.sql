-- Migration: Alert rules accept new condition and destination types
-- Version: V037
-- Database: SQLite

-- V026 pinned condition_type and destination_type with CHECK constraints, so every
-- new type (model_fallback_count, slack, discord) failed on insert. The API already
-- validates both against alert_service.CONDITION_TYPES and DESTINATION_TYPES, and
-- tables made by create_all never had the checks, so they are dropped rather than
-- widened. SQLite cannot drop a CHECK in place: the table is rebuilt.

CREATE TABLE alert_rules_v037 (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               VARCHAR(255) NOT NULL,
    description        TEXT,
    scope              VARCHAR(20)  NOT NULL CHECK (scope IN ('user', 'agent', 'project', 'global')),
    scope_id           VARCHAR(255),
    condition_type     VARCHAR(50)  NOT NULL,
    condition_config   TEXT,
    destination_type   VARCHAR(50)  NOT NULL,
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

INSERT INTO alert_rules_v037 (id, name, description, scope, scope_id, condition_type,
                              condition_config, destination_type, destination_config,
                              cooldown_seconds, is_enabled, last_fired_at, last_state,
                              last_error, fire_count, created_by, created_at, updated_at)
SELECT id, name, description, scope, scope_id, condition_type,
       condition_config, destination_type, destination_config,
       cooldown_seconds, is_enabled, last_fired_at, last_state,
       last_error, fire_count, created_by, created_at, updated_at
FROM alert_rules;

DROP TABLE alert_rules;

ALTER TABLE alert_rules_v037 RENAME TO alert_rules;

CREATE INDEX IF NOT EXISTS idx_ar_is_enabled     ON alert_rules(is_enabled);
CREATE INDEX IF NOT EXISTS idx_ar_scope          ON alert_rules(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_ar_condition_type ON alert_rules(condition_type);
