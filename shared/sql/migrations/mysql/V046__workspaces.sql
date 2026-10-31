-- Migration: workspaces, phase 1 (schema, no behaviour change)
-- Version: V046
-- Database: MySQL
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

CREATE TABLE IF NOT EXISTS workspaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    slug VARCHAR(40) NOT NULL UNIQUE,
    allow_platform_keys BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL
);

INSERT IGNORE INTO workspaces (id, name, slug) VALUES (1, 'Default', 'default');

CREATE TABLE IF NOT EXISTS workspace_members (
    id INT AUTO_INCREMENT PRIMARY KEY,
    workspace_id INT NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    agent_roles TEXT NOT NULL DEFAULT ('["user"]'),
    invited_by VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_workspace_members (workspace_id, user_id),
    CONSTRAINT fk_workspace_members_workspace FOREIGN KEY (workspace_id)
        REFERENCES workspaces(id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_members_user FOREIGN KEY (user_id)
        REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE INDEX idx_workspace_members_user_id ON workspace_members(user_id);

ALTER TABLE projects ADD COLUMN workspace_id INT NOT NULL DEFAULT 1,
    ADD CONSTRAINT fk_projects_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces(id);

ALTER TABLE test_cases ADD COLUMN workspace_id INT NOT NULL DEFAULT 1;
ALTER TABLE rate_limit_config ADD COLUMN workspace_id INT NOT NULL DEFAULT 1;
ALTER TABLE alert_rules ADD COLUMN workspace_id INT NOT NULL DEFAULT 1;
ALTER TABLE personal_access_tokens ADD COLUMN workspace_id INT NOT NULL DEFAULT 1;

ALTER TABLE token_usage_logs ADD COLUMN workspace_id INT;
ALTER TABLE guardrail_logs ADD COLUMN workspace_id INT;
ALTER TABLE agent_responses ADD COLUMN workspace_id INT;
ALTER TABLE audit_logs ADD COLUMN workspace_id INT;

CREATE INDEX idx_token_usage_logs_workspace_id ON token_usage_logs(workspace_id);
CREATE INDEX idx_guardrail_logs_workspace_id ON guardrail_logs(workspace_id);
CREATE INDEX idx_agent_responses_workspace_id ON agent_responses(workspace_id);
CREATE INDEX idx_audit_logs_workspace_id ON audit_logs(workspace_id);
