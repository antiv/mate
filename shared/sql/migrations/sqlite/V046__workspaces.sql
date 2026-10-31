-- Migration: workspaces, phase 1 (schema, no behaviour change)
-- Version: V046
-- Database: SQLite
--
-- 2.0 lets several organisations share one installation (documents/design/workspaces.md).
-- This adds the workspace tables and the workspace_id columns. Nothing reads them yet,
-- so the installation behaves as before: phase 2 of the 2.0 plan does the scoping.
--
-- - Workspace 1, "Default", holds everything that exists today.
-- - Projects, test cases, rate limits, alert rules and personal access tokens belong to
--   it through a NOT NULL DEFAULT 1 column, which fills the existing rows.
-- - The four log tables get a nullable workspace_id: an installation-wide audit entry
--   has none. It is written with each new row; older rows are filled in from their
--   project_id by shared/utils/log_projects.py, in batches, when the auth server starts.
-- - Members are not inserted here. The auth server adds every user to the default
--   workspace when it starts, and on every insert or role change after that
--   (shared/utils/workspaces.py), because the member role comes from users.roles,
--   a JSON list that each dialect reads differently.
--
-- SQLite refuses a REFERENCES column with a non-NULL default on ALTER TABLE, so
-- projects.workspace_id has no foreign key here; PostgreSQL and MySQL have one.

CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL UNIQUE,
    slug VARCHAR(40) NOT NULL UNIQUE,
    allow_platform_keys BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME
);

INSERT OR IGNORE INTO workspaces (id, name, slug) VALUES (1, 'Default', 'default');

CREATE TABLE IF NOT EXISTS workspace_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    agent_roles TEXT NOT NULL DEFAULT '["user"]',
    invited_by VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user_id ON workspace_members(user_id);

ALTER TABLE projects ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_projects_workspace_id ON projects(workspace_id);

ALTER TABLE test_cases ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE rate_limit_config ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE alert_rules ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1;
ALTER TABLE personal_access_tokens ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1;

ALTER TABLE token_usage_logs ADD COLUMN workspace_id INTEGER;
ALTER TABLE guardrail_logs ADD COLUMN workspace_id INTEGER;
ALTER TABLE agent_responses ADD COLUMN workspace_id INTEGER;
ALTER TABLE audit_logs ADD COLUMN workspace_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_token_usage_logs_workspace_id ON token_usage_logs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_logs_workspace_id ON guardrail_logs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_agent_responses_workspace_id ON agent_responses(workspace_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_workspace_id ON audit_logs(workspace_id);
