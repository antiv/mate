-- Migration: External chat platform integrations (Slack, Discord, ...)
-- Version: V022
-- Database: MySQL

CREATE TABLE IF NOT EXISTS channel_integrations (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    platform        VARCHAR(30) NOT NULL DEFAULT 'slack',
    project_id      INT NOT NULL,
    agent_name      VARCHAR(255) NOT NULL,
    label           VARCHAR(255),
    team_id         VARCHAR(64),
    bot_token       VARCHAR(255),
    signing_secret  VARCHAR(255),
    config          TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    INDEX idx_channel_integrations_team_id (team_id),
    INDEX idx_channel_integrations_platform (platform)
);
