-- Migration: workspaces, phase 4a (schema for provider keys per workspace)
-- Version: V049
-- Database: PostgreSQL
--
-- Phase 4 of 2.0 gives a workspace its own model provider keys (documents/design/2.0-plan.md).
-- Nothing uses these yet:
--
-- - workspace_provider_keys: a workspace's key for one provider (google, openai,
--   openrouter, ...), with an optional base URL. api_key is encrypted with
--   MATE_SECRET_KEY (shared/utils/secret_box.py), so it is TEXT, as in V045.
-- - workspace_settings: a workspace's own value for a setting that the installation
--   keeps in system_settings, starting with the image model. A table of its own:
--   system_settings is keyed by setting_key alone, and holds the installation's
--   own flags.

CREATE TABLE IF NOT EXISTS workspace_provider_keys (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider VARCHAR(50) NOT NULL,
    api_key TEXT NOT NULL,
    base_url VARCHAR(1024),
    updated_by VARCHAR(255),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_workspace_provider_keys UNIQUE (workspace_id, provider)
);

CREATE TABLE IF NOT EXISTS workspace_settings (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    setting_key VARCHAR(100) NOT NULL,
    value TEXT,
    updated_by VARCHAR(255),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, setting_key)
);
