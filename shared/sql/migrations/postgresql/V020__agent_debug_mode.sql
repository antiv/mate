-- Migration: Add debug_mode flag to agents_config
-- Version: V020
-- Database: PostgreSQL

ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS debug_mode BOOLEAN NOT NULL DEFAULT FALSE;
