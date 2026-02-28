-- Migration: widget_api_keys
-- Version: V004
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS widget_api_keys (
    id INT AUTO_INCREMENT PRIMARY KEY,
    api_key VARCHAR(255) NOT NULL UNIQUE,
    project_id INT NOT NULL,
    agent_name VARCHAR(255) NOT NULL,
    label VARCHAR(255),
    allowed_origins TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    widget_config TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    INDEX idx_widget_api_keys_api_key (api_key),
    INDEX idx_widget_api_keys_project_id (project_id)
);
