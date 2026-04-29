-- Migration: OAuth user profile columns
-- Version: V013
-- Database: SQLite

ALTER TABLE users ADD COLUMN email VARCHAR(255);
ALTER TABLE users ADD COLUMN display_name VARCHAR(255);
ALTER TABLE users ADD COLUMN oauth_provider VARCHAR(50);
