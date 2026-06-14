-- Migration: Expose agents as models and support Personal Access Tokens
-- Version: V014
-- Database: SQLite

ALTER TABLE agents_config ADD COLUMN expose_as_model BOOLEAN NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    token_prefix VARCHAR(16) NOT NULL,
    name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_used_at TIMESTAMP,
    expires_at TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_personal_access_tokens_token_hash ON personal_access_tokens(token_hash);
