-- Migration: workspaces, phase 3b (schema for workspace management)
-- Version: V048
-- Database: PostgreSQL
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
--   projects_name_key is the name PostgreSQL gave V001's inline UNIQUE, and create_all's.

CREATE TABLE IF NOT EXISTS workspace_invitations (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    accepted_at TIMESTAMP,
    accepted_by VARCHAR(255),
    created_by VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace_id ON workspace_invitations(workspace_id);

ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMP;
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS platform_features TEXT NOT NULL DEFAULT '[]';
UPDATE workspaces SET platform_features = '["code", "server_credentials", "email"]' WHERE id = 1;

ALTER TABLE projects ADD CONSTRAINT uq_projects_workspace_name UNIQUE (workspace_id, name);
ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_name_key;
