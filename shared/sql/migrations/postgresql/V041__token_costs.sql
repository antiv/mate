-- Migration: Token costs in US dollars
-- Version: V041
-- Database: PostgreSQL
--
-- Every model call is priced when it is logged: cost_usd is its cost in US dollars,
-- NULL when the model has no known price (never 0 for "unknown"). is_fallback marks
-- a call the agent's fallback model answered, so its cost can be shown on its own.
-- model_prices holds the prices an admin sets for models no price list covers
-- (Settings page); they win over the prices LiteLLM and OpenRouter publish.

ALTER TABLE token_usage_logs ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION;
ALTER TABLE token_usage_logs ADD COLUMN IF NOT EXISTS is_fallback BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS model_prices (
    model_name             VARCHAR(255) NOT NULL PRIMARY KEY,
    input_usd_per_mtok     DOUBLE PRECISION NOT NULL,
    output_usd_per_mtok    DOUBLE PRECISION NOT NULL,
    updated_by             VARCHAR(255),
    updated_at             TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
