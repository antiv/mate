-- Migration: RBAC secure-by-default backfill
-- Version: V017
-- Database: MySQL
--
-- After this migration, agents with no roles configured are admin-only (enforced in
-- rbac_middleware). These backfills preserve current access for existing data and isolate
-- the embeddable widget from dashboard 'user' accounts.

-- 1) Existing agents with no roles -> keep current dashboard access (admin + user)
UPDATE agents_config SET allowed_for_roles = '["admin", "user"]'
WHERE allowed_for_roles IS NULL OR allowed_for_roles = '' OR allowed_for_roles = '[]';

-- 2) Widget-bound agents -> widget audience (admin for testing + widget visitors)
UPDATE agents_config SET allowed_for_roles = '["admin", "widget"]'
WHERE name IN (SELECT agent_name FROM widget_api_keys);

-- 3) Existing public widget visitors -> dedicated 'widget' role
UPDATE users SET roles = '["widget"]' WHERE user_id LIKE 'widget\_%' ESCAPE '\\';
