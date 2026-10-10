-- Migration: OAuth user profile columns
-- Version: V013
-- Database: MySQL

ALTER TABLE users
    ADD COLUMN email VARCHAR(255),
    ADD COLUMN display_name VARCHAR(255),
    ADD COLUMN oauth_provider VARCHAR(50);
