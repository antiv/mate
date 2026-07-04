-- Migration: External chat platform integrations (Slack, Discord, ...)
-- Version: V022
-- Database: SQLite

CREATE TABLE IF NOT EXISTS channel_integrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        VARCHAR(30) NOT NULL DEFAULT 'slack',
    project_id      INTEGER NOT NULL,
    agent_name      VARCHAR(255) NOT NULL,
    label           VARCHAR(255),
    team_id         VARCHAR(64),
    bot_token       VARCHAR(255),
    signing_secret  VARCHAR(255),
    config          TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channel_integrations_team_id ON channel_integrations (team_id);
CREATE INDEX IF NOT EXISTS idx_channel_integrations_platform ON channel_integrations (platform);
