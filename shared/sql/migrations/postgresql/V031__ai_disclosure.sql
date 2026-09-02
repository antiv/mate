-- Migration: AI disclosure for agents_config (EU AI Act Art. 50)
-- Version: V031
-- Database: PostgreSQL
--
-- Art. 50 requires that people are told they are interacting with an AI.
-- ai_disclosure NULL means the default text is shown; setting ai_disclosure_waiver
-- turns the disclosure off and records why, so it cannot be disabled silently.

ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS ai_disclosure TEXT NULL;
ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS ai_disclosure_waiver TEXT NULL;
