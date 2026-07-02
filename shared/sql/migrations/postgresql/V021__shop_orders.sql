-- Migration: Shop orders placed via the e-commerce MCP server
-- Version: V021
-- Database: PostgreSQL

CREATE TABLE IF NOT EXISTS shop_orders (
    id              SERIAL PRIMARY KEY,
    order_id        VARCHAR(32) NOT NULL,
    partner_key     VARCHAR(100),
    shop_name       VARCHAR(255),
    customer_name   VARCHAR(255),
    customer_email  VARCHAR(320),
    items           TEXT,
    currency        VARCHAR(10),
    total           DOUBLE PRECISION,
    note            TEXT,
    status          VARCHAR(30) NOT NULL DEFAULT 'new',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shop_orders_order_id ON shop_orders (order_id);
CREATE INDEX IF NOT EXISTS idx_shop_orders_partner_key ON shop_orders (partner_key);
