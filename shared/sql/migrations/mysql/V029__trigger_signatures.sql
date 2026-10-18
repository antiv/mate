-- Migration: Optional HMAC signature verification on webhook triggers
-- Version: V029
-- Database: MySQL

ALTER TABLE agent_triggers ADD COLUMN signing_secret VARCHAR(255);
ALTER TABLE agent_triggers ADD COLUMN require_signature TINYINT(1) NOT NULL DEFAULT 0;
