-- Migration: Store an agent JSON snapshot on the wizard lead
-- Version: V016
-- Database: MySQL

ALTER TABLE wizard_leads ADD COLUMN agent_snapshot TEXT;
