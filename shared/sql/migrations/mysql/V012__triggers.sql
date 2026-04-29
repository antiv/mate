-- Migration: Trigger Engine
-- Version: V012
-- Database: MySQL

CREATE TABLE IF NOT EXISTS agent_triggers (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    trigger_type    VARCHAR(50)  NOT NULL DEFAULT 'cron',
    agent_name      VARCHAR(255) NOT NULL,
    project_id      INT          NOT NULL,
    prompt          TEXT         NOT NULL,
    cron_expression VARCHAR(100),
    webhook_path    VARCHAR(255) UNIQUE,
    fire_key_hash   VARCHAR(255),
    output_type     VARCHAR(50)  NOT NULL DEFAULT 'memory_block',
    output_config   TEXT,
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
    last_fired_at   DATETIME,
    last_result     TEXT,
    created_by      VARCHAR(255),
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_at_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_at_agent_name   (agent_name),
    INDEX idx_at_project_id   (project_id),
    INDEX idx_at_trigger_type (trigger_type),
    INDEX idx_at_is_enabled   (is_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
