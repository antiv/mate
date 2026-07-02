-- Migration: Shop orders placed via the e-commerce MCP server
-- Version: V021
-- Database: MySQL

CREATE TABLE IF NOT EXISTS shop_orders (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    order_id        VARCHAR(32) NOT NULL,
    partner_key     VARCHAR(100),
    shop_name       VARCHAR(255),
    customer_name   VARCHAR(255),
    customer_email  VARCHAR(320),
    items           TEXT,
    currency        VARCHAR(10),
    total           DOUBLE,
    note            TEXT,
    status          VARCHAR(30) NOT NULL DEFAULT 'new',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    INDEX idx_shop_orders_order_id (order_id),
    INDEX idx_shop_orders_partner_key (partner_key)
);
