-- Migration: Dollar budgets that see unpriced calls and sub-agents
-- Version: V043
-- Database: SQLite
--
-- block_unpriced: a dollar budget treats calls without a price in its period as
-- over budget, instead of not counting them.
-- include_sub_agents: an agent's budgets count what its sub-agents use too.

ALTER TABLE rate_limit_config ADD COLUMN block_unpriced BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE rate_limit_config ADD COLUMN include_sub_agents BOOLEAN NOT NULL DEFAULT 0;
