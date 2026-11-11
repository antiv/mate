-- Migration: workspaces, phase 3b (schema for workspace management)
-- Version: V048
-- Database: SQLite
--
-- Phase 3 of 2.0 lets workspaces be set up from the dashboard (documents/design/2.0-plan.md).
-- Nothing in the dashboard uses these yet:
--
-- - workspace_invitations: an invitation for one email to one workspace, with a role.
--   Only the token's hash is stored; a row is used once (accepted_at).
-- - workspaces.suspended_at: a suspended workspace stops, and can be resumed.
--   deleted_at stays what closes one.
-- - workspaces.platform_features: the platform-only features a platform administrator
--   enabled for the workspace, a JSON list of "code", "server_credentials" and
--   "email". The default workspace has all three, which is what 1.x does.
-- - projects.name is unique per workspace instead of across the installation.
--
-- SQLite cannot drop the UNIQUE that V001 put on projects.name, so the table is rebuilt.
-- DROP TABLE would first delete every project and, with foreign keys on, every agent,
-- memory block, trigger and widget key with it: the migration runner turns foreign
-- keys off for SQLite migrations (shared/utils/migration_system.py). The children keep
-- their REFERENCES projects(id), which the renamed table answers.

CREATE TABLE IF NOT EXISTS workspace_invitations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    accepted_at DATETIME,
    accepted_by VARCHAR(255),
    created_by VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace_id ON workspace_invitations(workspace_id);

ALTER TABLE workspaces ADD COLUMN suspended_at DATETIME;
ALTER TABLE workspaces ADD COLUMN platform_features TEXT NOT NULL DEFAULT '[]';
UPDATE workspaces SET platform_features = '["code", "server_credentials", "email"]' WHERE id = 1;

CREATE TABLE projects_v048 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    template_id VARCHAR(255) NULL,
    template_version VARCHAR(50) NULL,
    template_prefix VARCHAR(255) NULL,
    workspace_id INTEGER NOT NULL DEFAULT 1 REFERENCES workspaces(id),
    CONSTRAINT uq_projects_workspace_name UNIQUE (workspace_id, name)
);

INSERT INTO projects_v048 (id, name, description, created_at, updated_at, template_id,
                           template_version, template_prefix, workspace_id)
SELECT id, name, description, created_at, updated_at, template_id,
       template_version, template_prefix, workspace_id
FROM projects;

DROP TABLE projects;

ALTER TABLE projects_v048 RENAME TO projects;

CREATE INDEX IF NOT EXISTS idx_projects_workspace_id ON projects(workspace_id);
