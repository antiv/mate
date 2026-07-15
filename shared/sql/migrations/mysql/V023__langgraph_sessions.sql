-- Migration: Sessions and event log for the LangGraph runtime (AGENT_FRAMEWORK=langgraph)
-- Version: V023
-- Database: MySQL

CREATE TABLE IF NOT EXISTS lg_sessions (
    id          VARCHAR(255) PRIMARY KEY,
    app_name    VARCHAR(255) NOT NULL,
    user_id     VARCHAR(255) NOT NULL,
    state       TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    INDEX idx_lg_sessions_app_user (app_name, user_id)
);

CREATE TABLE IF NOT EXISTS lg_events (
    id              VARCHAR(255) PRIMARY KEY,
    session_id      VARCHAR(255) NOT NULL,
    author          VARCHAR(255),
    invocation_id   VARCHAR(255),
    content         TEXT,
    actions         TEXT,
    usage_metadata  TEXT,
    timestamp       DOUBLE NOT NULL,
    INDEX idx_lg_events_session_id (session_id),
    CONSTRAINT fk_lg_events_session FOREIGN KEY (session_id) REFERENCES lg_sessions(id) ON DELETE CASCADE
);
