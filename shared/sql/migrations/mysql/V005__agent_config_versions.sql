-- Migration: agent_config_versions for config versioning, diff, and rollback
-- Version: V005
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS agent_config_versions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agent_config_id INT NOT NULL,
    version_number INT NOT NULL,
    config_snapshot TEXT NOT NULL,
    changed_by VARCHAR(255),
    change_type VARCHAR(50) NOT NULL DEFAULT 'update',
    tag VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_config_id) REFERENCES agents_config(id) ON DELETE CASCADE,
    INDEX idx_acv_agent_config_id (agent_config_id),
    INDEX idx_acv_version_number (agent_config_id, version_number),
    INDEX idx_acv_tag (tag)
);
