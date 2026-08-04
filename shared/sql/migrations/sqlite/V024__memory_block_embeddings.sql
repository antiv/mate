-- Migration: Embedding columns on memory_blocks for semantic search
-- Version: V024
-- Database: SQLite

ALTER TABLE memory_blocks ADD COLUMN embedding TEXT;
ALTER TABLE memory_blocks ADD COLUMN embedding_model TEXT;
ALTER TABLE memory_blocks ADD COLUMN embedding_hash TEXT;
