-- Migration: project_id on log rows
-- Version: V044
-- Database: SQLite
--
-- Every row of the four log tables carries the project it belongs to, written when
-- the row is written: the agent's project for token usage, guardrail hits and
-- responses; for audit entries the project of the agent or project they name, NULL
-- for installation-wide ones. Usage per project then survives agents being
-- renamed, moved or deleted, and 2.0 can scope logs by workspace through it.
-- No foreign key: a row keeps its project id after the project is deleted.
--
-- Rows written before this migration are filled in by shared/utils/log_projects.py,
-- in batches, when the auth server starts; that is not done here, so a large
-- token_usage_logs is not locked for the length of one UPDATE.

ALTER TABLE token_usage_logs ADD COLUMN project_id INTEGER;
ALTER TABLE guardrail_logs ADD COLUMN project_id INTEGER;
ALTER TABLE agent_responses ADD COLUMN project_id INTEGER;
ALTER TABLE audit_logs ADD COLUMN project_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_token_usage_logs_project_id ON token_usage_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_logs_project_id ON guardrail_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_responses_project_id ON agent_responses(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_project_id ON audit_logs(project_id);
