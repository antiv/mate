-- Migration: Agent Responses (per-invocation latency)
-- Version: V027
-- Database: MySQL

CREATE TABLE IF NOT EXISTS agent_responses (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    invocation_id VARCHAR(255) NOT NULL,
    session_id    VARCHAR(255),
    user_id       VARCHAR(255),
    agent_name    VARCHAR(255),
    origin        VARCHAR(20)  NOT NULL DEFAULT 'chat',
    started_at    DATETIME     NOT NULL,
    duration_ms   INT,
    status        VARCHAR(50)  NOT NULL DEFAULT 'SUCCESS',
    INDEX idx_ares_invocation (invocation_id),
    INDEX idx_ares_session    (session_id),
    INDEX idx_ares_agent_time (agent_name, started_at),
    INDEX idx_ares_origin     (origin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
