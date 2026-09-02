-- Migration: Optional HMAC signature verification on webhook triggers
-- Version: V029
-- Database: PostgreSQL

ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS signing_secret VARCHAR(255);
ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS require_signature BOOLEAN NOT NULL DEFAULT FALSE;
