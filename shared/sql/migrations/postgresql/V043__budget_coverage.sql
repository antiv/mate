-- Migration: Dollar budgets that see unpriced calls and sub-agents
-- Version: V043
-- Database: PostgreSQL
--
-- block_unpriced: a dollar budget treats calls without a price in its period as
-- over budget, instead of not counting them.
-- include_sub_agents: an agent's budgets count what its sub-agents use too.

ALTER TABLE rate_limit_config ADD COLUMN IF NOT EXISTS block_unpriced BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rate_limit_config ADD COLUMN IF NOT EXISTS include_sub_agents BOOLEAN NOT NULL DEFAULT FALSE;
