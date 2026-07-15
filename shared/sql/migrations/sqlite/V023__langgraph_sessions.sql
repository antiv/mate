-- Migration: Sessions and event log for the LangGraph runtime (AGENT_FRAMEWORK=langgraph)
-- Version: V023
-- Database: SQLite

CREATE TABLE IF NOT EXISTS lg_sessions (
    id          VARCHAR(255) PRIMARY KEY,
    app_name    VARCHAR(255) NOT NULL,
    user_id     VARCHAR(255) NOT NULL,
    state       TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lg_sessions_app_user ON lg_sessions (app_name, user_id);

CREATE TABLE IF NOT EXISTS lg_events (
    id              VARCHAR(255) PRIMARY KEY,
    session_id      VARCHAR(255) NOT NULL REFERENCES lg_sessions(id) ON DELETE CASCADE,
    author          VARCHAR(255),
    invocation_id   VARCHAR(255),
    content         TEXT,
    actions         TEXT,
    usage_metadata  TEXT,
    timestamp       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lg_events_session_id ON lg_events (session_id);
