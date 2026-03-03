-- Migration: agent_config_versions for config versioning, diff, and rollback
-- Version: V005
-- Database: POSTGRESQL

CREATE TABLE IF NOT EXISTS agent_config_versions (
    id SERIAL PRIMARY KEY,
    agent_config_id INTEGER NOT NULL REFERENCES agents_config(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    config_snapshot TEXT NOT NULL,
    changed_by VARCHAR(255),
    change_type VARCHAR(50) NOT NULL DEFAULT 'update',
    tag VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_acv_agent_config_id ON agent_config_versions(agent_config_id);
CREATE INDEX IF NOT EXISTS idx_acv_version_number ON agent_config_versions(agent_config_id, version_number);
CREATE INDEX IF NOT EXISTS idx_acv_tag ON agent_config_versions(tag);
