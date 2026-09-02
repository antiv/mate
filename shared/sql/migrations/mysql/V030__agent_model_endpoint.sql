-- Migration: Per-agent OpenAI-compatible endpoint for agents_config
-- Version: V030
-- Database: MySQL
--
-- Without these the endpoint came only from provider env vars, so every agent
-- shared one endpoint per provider and none could be pointed at an agent
-- running elsewhere. model_api_key may hold a ${VAR} reference instead of the
-- secret itself.

ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS model_base_url VARCHAR(1024) NULL;
ALTER TABLE agents_config ADD COLUMN IF NOT EXISTS model_api_key VARCHAR(1024) NULL;
