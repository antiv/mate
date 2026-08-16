-- Migration: Response Feedback (thumbs up/down)
-- Version: V028
-- Database: MySQL

CREATE TABLE IF NOT EXISTS response_feedback (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    message_id VARCHAR(255) NOT NULL,
    agent_name VARCHAR(255),
    project_id INT,
    rating     VARCHAR(10)  NOT NULL,
    comment    TEXT,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_rf_session_message UNIQUE (session_id, message_id),
    CONSTRAINT fk_rf_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_rf_agent_time (agent_name, created_at),
    INDEX idx_rf_project    (project_id),
    INDEX idx_rf_rating     (rating)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
