-- Migration: Add template sync tracking columns to projects
-- Version: V010
-- Created: 2024-03-10
-- Database: POSTGRESQL

ALTER TABLE projects ADD COLUMN IF NOT EXISTS template_id VARCHAR(255) NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS template_version VARCHAR(50) NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS template_prefix VARCHAR(255) NULL;
