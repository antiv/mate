-- Migration: Budgets in US dollars
-- Version: V042
-- Database: MySQL
--
-- Budgets in US dollars next to the token budgets: what a user, agent or project
-- may spend per day (last 24 hours) and per month (last 30 days), at the prices
-- token_usage_logs.cost_usd records (V041). Calls whose model has no price do not
-- count toward them.

ALTER TABLE rate_limit_config ADD COLUMN usd_per_day DOUBLE;
ALTER TABLE rate_limit_config ADD COLUMN usd_per_month DOUBLE;
