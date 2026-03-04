-- Migration: audit_logs (append-only audit trail for EU AI Act compliance)
-- Version: V009
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS audit_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor VARCHAR(255) NOT NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id VARCHAR(500),
    details JSON,
    ip_address VARCHAR(45),
    INDEX idx_audit_logs_timestamp (timestamp),
    INDEX idx_audit_logs_actor (actor),
    INDEX idx_audit_logs_action (action),
    INDEX idx_audit_logs_resource_type (resource_type),
    INDEX idx_audit_logs_resource_id (resource_id)
);
