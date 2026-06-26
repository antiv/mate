-- Migration: Wizard partners (per-site pricing, origin allowlist, lead attribution)
-- Version: V019
-- Database: SQLite

CREATE TABLE IF NOT EXISTS wizard_partners (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_key     VARCHAR(100) UNIQUE NOT NULL,
    name            VARCHAR(255),
    allowed_origins TEXT,                       -- JSON array of origins; null/empty = any
    contact_email   VARCHAR(320),
    default_lang    VARCHAR(10),
    pricing         TEXT,                       -- JSON {default_currency, prices:{tier:{cur:str}}}
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

ALTER TABLE wizard_sessions ADD COLUMN partner_key VARCHAR(100);
ALTER TABLE wizard_leads ADD COLUMN partner_key VARCHAR(100);
