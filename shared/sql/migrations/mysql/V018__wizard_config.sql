-- Migration: Wizard config key-value store (e.g. tier pricing edited from the dashboard)
-- Version: V018
-- Database: MySQL

CREATE TABLE IF NOT EXISTS wizard_config (
    config_key   VARCHAR(100) PRIMARY KEY,
    config_value TEXT,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
) ENGINE=InnoDB;
