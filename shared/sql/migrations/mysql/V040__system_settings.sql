-- Migration: System settings
-- Version: V040
-- Database: MySQL
--
-- Server-wide settings an admin changes in the dashboard (Settings page), such as
-- the default image model. A stored value wins over the matching environment
-- variable; deleting the row falls back to it.

CREATE TABLE IF NOT EXISTS system_settings (
    setting_key VARCHAR(100) NOT NULL PRIMARY KEY,
    value       TEXT,
    updated_by  VARCHAR(255),
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
