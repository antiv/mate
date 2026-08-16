-- Migration: Response Feedback (thumbs up/down)
-- Version: V028
-- Database: SQLite

CREATE TABLE IF NOT EXISTS response_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(255) NOT NULL,
    message_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255),
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    rating     VARCHAR(10)  NOT NULL CHECK (rating IN ('up', 'down')),
    comment    TEXT,
    created_at DATETIME     NOT NULL DEFAULT (datetime('now')),
    updated_at DATETIME     NOT NULL DEFAULT (datetime('now')),
    CONSTRAINT uq_rf_session_message UNIQUE (session_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_rf_agent_time ON response_feedback(agent_name, created_at);
CREATE INDEX IF NOT EXISTS idx_rf_project    ON response_feedback(project_id);
CREATE INDEX IF NOT EXISTS idx_rf_rating     ON response_feedback(rating);
