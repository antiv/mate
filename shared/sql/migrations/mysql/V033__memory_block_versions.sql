-- Migration: memory_block_versions for memory block history and restore
-- Version: V033
-- Database: MySQL
--
-- One row per write to a memory block, holding the block as it was after the
-- write. block_id has no foreign key: a deleted block keeps its history, which
-- is what lets it be restored.

CREATE TABLE IF NOT EXISTS memory_block_versions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id INT NOT NULL,
    block_id INT NOT NULL,
    version_number INT NOT NULL,
    label VARCHAR(500) NOT NULL,
    value TEXT NOT NULL,
    description TEXT,
    metadata TEXT,
    changed_by VARCHAR(255),
    change_type VARCHAR(50) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    INDEX idx_mbv_block (block_id, version_number),
    INDEX idx_mbv_project (project_id)
);
