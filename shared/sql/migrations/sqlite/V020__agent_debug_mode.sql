-- Migration: Add debug_mode flag to agents_config
-- Version: V020
-- Database: SQLite

ALTER TABLE agents_config ADD COLUMN debug_mode BOOLEAN NOT NULL DEFAULT 0;
