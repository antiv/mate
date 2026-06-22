-- Migration: Store an agent JSON snapshot on the wizard lead
-- Version: V016
-- Database: SQLite

ALTER TABLE wizard_leads ADD COLUMN agent_snapshot TEXT;
