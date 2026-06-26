-- Migration: Wizard partners (per-site pricing, origin allowlist, lead attribution)
-- Version: V019
-- Database: PostgreSQL

CREATE TABLE IF NOT EXISTS wizard_partners (
    id              SERIAL PRIMARY KEY,
    partner_key     VARCHAR(100) UNIQUE NOT NULL,
    name            VARCHAR(255),
    allowed_origins TEXT,
    contact_email   VARCHAR(320),
    default_lang    VARCHAR(10),
    pricing         TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

ALTER TABLE wizard_sessions ADD COLUMN IF NOT EXISTS partner_key VARCHAR(100);
ALTER TABLE wizard_leads ADD COLUMN IF NOT EXISTS partner_key VARCHAR(100);
