-- Migration: Eval framework (test cases and eval results)
-- Version: V011
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS test_cases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    version_id INT,
    input TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    eval_method VARCHAR(50) NOT NULL DEFAULT 'exact_match',
    judge_model VARCHAR(255),
    threshold FLOAT NOT NULL DEFAULT 0.7,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255),
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    FOREIGN KEY (version_id) REFERENCES agent_config_versions(id) ON DELETE SET NULL,
    INDEX idx_tc_agent_name (agent_name),
    INDEX idx_tc_version_id (version_id),
    INDEX idx_tc_is_active (is_active)
);

CREATE TABLE IF NOT EXISTS eval_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    test_case_id INT NOT NULL,
    version_id INT NOT NULL,
    actual_output TEXT,
    score FLOAT,
    passed TINYINT(1),
    eval_method VARCHAR(50) NOT NULL,
    details TEXT,
    error TEXT,
    run_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES agent_config_versions(id) ON DELETE CASCADE,
    INDEX idx_er_test_case_id (test_case_id),
    INDEX idx_er_version_id (version_id),
    INDEX idx_er_run_at (run_at)
);
