-- Migration: Embedding columns on memory_blocks for semantic search
-- Version: V024
-- Database: MySQL

ALTER TABLE memory_blocks ADD COLUMN embedding TEXT;
ALTER TABLE memory_blocks ADD COLUMN embedding_model VARCHAR(200);
ALTER TABLE memory_blocks ADD COLUMN embedding_hash VARCHAR(64);
