-- Migration: Fallback model for agents_config
-- Version: V034
-- Database: MySQL
--
-- A second model to re-run a request on when the agent's own model still fails
-- after its retries, so a provider outage does not reach the person chatting.
-- Uses the provider env vars, never the agent's model_base_url / model_api_key.

ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS fallback_model VARCHAR(255) NULL;
