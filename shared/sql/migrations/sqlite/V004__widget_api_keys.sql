-- Migration: widget_api_keys
-- Version: V004
-- Database: SQLITE

CREATE TABLE IF NOT EXISTS widget_api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key VARCHAR(255) NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    agent_name VARCHAR(255) NOT NULL,
    label VARCHAR(255),
    allowed_origins TEXT,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    widget_config TEXT,
    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_widget_api_keys_api_key ON widget_api_keys(api_key);
CREATE INDEX IF NOT EXISTS idx_widget_api_keys_project_id ON widget_api_keys(project_id);
