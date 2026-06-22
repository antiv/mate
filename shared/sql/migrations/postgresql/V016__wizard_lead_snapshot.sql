-- Migration: Store an agent JSON snapshot on the wizard lead
-- Version: V016
-- Database: PostgreSQL

ALTER TABLE wizard_leads ADD COLUMN IF NOT EXISTS agent_snapshot TEXT;
