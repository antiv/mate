-- Migration: Embedding columns on memory_blocks for semantic search
-- Version: V024
-- Database: PostgreSQL

ALTER TABLE memory_blocks ADD COLUMN IF NOT EXISTS embedding TEXT;
ALTER TABLE memory_blocks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(200);
ALTER TABLE memory_blocks ADD COLUMN IF NOT EXISTS embedding_hash VARCHAR(64);
