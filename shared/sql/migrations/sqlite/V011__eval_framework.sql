-- Migration: Eval framework (test cases and eval results)
-- Version: V011
-- Database: SQLITE

CREATE TABLE IF NOT EXISTS test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name VARCHAR(255) NOT NULL,
    version_id INTEGER,
    input TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    eval_method VARCHAR(50) NOT NULL DEFAULT 'exact_match',
    judge_model VARCHAR(255),
    threshold REAL NOT NULL DEFAULT 0.7,
    created_at DATETIME NOT NULL DEFAULT (datetime('now')),
    created_by VARCHAR(255),
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (version_id) REFERENCES agent_config_versions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tc_agent_name ON test_cases(agent_name);
CREATE INDEX IF NOT EXISTS idx_tc_version_id ON test_cases(version_id);
CREATE INDEX IF NOT EXISTS idx_tc_is_active ON test_cases(is_active);

CREATE TABLE IF NOT EXISTS eval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id INTEGER NOT NULL,
    version_id INTEGER NOT NULL,
    actual_output TEXT,
    score REAL,
    passed INTEGER,
    eval_method VARCHAR(50) NOT NULL,
    details TEXT,
    error TEXT,
    run_at DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES agent_config_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_er_test_case_id ON eval_results(test_case_id);
CREATE INDEX IF NOT EXISTS idx_er_version_id ON eval_results(version_id);
CREATE INDEX IF NOT EXISTS idx_er_run_at ON eval_results(run_at);
