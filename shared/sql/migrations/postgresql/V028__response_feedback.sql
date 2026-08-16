-- Migration: Response Feedback (thumbs up/down)
-- Version: V028
-- Database: PostgreSQL

CREATE TABLE IF NOT EXISTS response_feedback (
    id         SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    message_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255),
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    rating     VARCHAR(10)  NOT NULL CHECK (rating IN ('up', 'down')),
    comment    TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rf_session_message UNIQUE (session_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_rf_agent_time ON response_feedback(agent_name, created_at);
CREATE INDEX IF NOT EXISTS idx_rf_project    ON response_feedback(project_id);
CREATE INDEX IF NOT EXISTS idx_rf_rating     ON response_feedback(rating);

-- update_updated_at_column() is created in V012; defined here too so this migration
-- does not depend on that one having run.
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $func$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$func$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_response_feedback_updated_at'
    ) THEN
        CREATE TRIGGER update_response_feedback_updated_at
            BEFORE UPDATE ON response_feedback
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
