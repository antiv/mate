-- Migration: Agent Responses (per-invocation latency)
-- Version: V027
-- Database: SQLite

CREATE TABLE IF NOT EXISTS agent_responses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id VARCHAR(255) NOT NULL,
    session_id    VARCHAR(255),
    user_id       VARCHAR(255),
    agent_name    VARCHAR(255),
    origin        VARCHAR(20)  NOT NULL DEFAULT 'chat',
    started_at    DATETIME     NOT NULL,
    duration_ms   INTEGER,
    status        VARCHAR(50)  NOT NULL DEFAULT 'SUCCESS'
);

CREATE INDEX IF NOT EXISTS idx_ares_invocation ON agent_responses(invocation_id);
CREATE INDEX IF NOT EXISTS idx_ares_session    ON agent_responses(session_id);
CREATE INDEX IF NOT EXISTS idx_ares_agent_time ON agent_responses(agent_name, started_at);
CREATE INDEX IF NOT EXISTS idx_ares_origin     ON agent_responses(origin);
