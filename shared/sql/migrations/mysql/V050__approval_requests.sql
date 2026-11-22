-- Migration: approvals inbox, phase 1 (the table)
-- Version: V050
-- Database: MySQL
--
-- One row per tool call an agent paused on for approval (require_confirmation),
-- written by the agent runtime when it emits the adk_request_confirmation call, on
-- either runtime and from every surface (documents/design/approvals-inbox.md).
-- Phase 1 only records: a row is decided when the answer comes back in the same
-- session, which today means a chat. Nothing reads the table to change a run yet.
--
-- project_id, workspace_id and trigger_id have no foreign key, as in the log tables:
-- the history of a decision stays when its agent, project or trigger is deleted.
-- (session_id, confirmation_call_id) is unique: the answer names the call, and a
-- confirmation id is only unique within its session on the LangGraph runtime.

CREATE TABLE IF NOT EXISTS approval_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    agent_name VARCHAR(255) NOT NULL,
    project_id INT,
    workspace_id INT,
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255) NOT NULL,
    invocation_id VARCHAR(255),
    confirmation_call_id VARCHAR(255) NOT NULL,
    tool_call_id VARCHAR(255),
    tool_name VARCHAR(255) NOT NULL,
    tool_args TEXT,
    hint TEXT,
    source VARCHAR(20) NOT NULL DEFAULT 'chat',
    trigger_id INT,
    requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    decided_by VARCHAR(255),
    decided_via VARCHAR(20),
    decided_at DATETIME,
    note TEXT,
    resume_status VARCHAR(20),
    resume_result TEXT,
    CONSTRAINT uq_approval_requests_call UNIQUE (session_id, confirmation_call_id),
    INDEX idx_approval_requests_status (status),
    INDEX idx_approval_requests_workspace_time (workspace_id, requested_at),
    INDEX idx_approval_requests_invocation (invocation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
