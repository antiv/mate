-- Migration: widget_api_keys
-- Version: V004
-- Database: POSTGRESQL

CREATE TABLE IF NOT EXISTS widget_api_keys (
    id SERIAL PRIMARY KEY,
    api_key VARCHAR(255) NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    agent_name VARCHAR(255) NOT NULL,
    label VARCHAR(255),
    allowed_origins TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    widget_config TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_widget_api_keys_api_key ON widget_api_keys(api_key);
CREATE INDEX IF NOT EXISTS idx_widget_api_keys_project_id ON widget_api_keys(project_id);
