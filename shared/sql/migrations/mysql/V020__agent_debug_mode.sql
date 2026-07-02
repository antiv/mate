-- Migration: Add debug_mode flag to agents_config
-- Version: V020
-- Database: MySQL

ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS debug_mode TINYINT(1) NOT NULL DEFAULT 0;
