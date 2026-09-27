-- Migration: memory_block_versions for memory block history and restore
-- Version: V033
-- Database: PostgreSQL
--
-- One row per write to a memory block, holding the block as it was after the
-- write. block_id has no foreign key: a deleted block keeps its history, which
-- is what lets it be restored.

CREATE TABLE IF NOT EXISTS memory_block_versions (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    block_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    label VARCHAR(500) NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    description TEXT,
    metadata TEXT,
    changed_by VARCHAR(255),
    change_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mbv_block ON memory_block_versions(block_id, version_number);
CREATE INDEX IF NOT EXISTS idx_mbv_project ON memory_block_versions(project_id);
