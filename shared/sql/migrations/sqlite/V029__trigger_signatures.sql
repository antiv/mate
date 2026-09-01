-- Migration: Optional HMAC signature verification on webhook triggers
-- Version: V029
-- Database: SQLite

ALTER TABLE agent_triggers ADD COLUMN signing_secret VARCHAR(255);
ALTER TABLE agent_triggers ADD COLUMN require_signature BOOLEAN NOT NULL DEFAULT 0;
