-- Migration: TEXT for the encrypted secret columns
-- Version: V045
-- Database: PostgreSQL
--
-- Secrets stored encrypted (MATE_SECRET_KEY, shared/utils/secret_box.py) are longer
-- than the plain value: a Fernet token plus its "enc:v1:" prefix is about 1.4 times
-- the secret and at least 107 characters, so a 255-character bot token no longer fits
-- VARCHAR(255) and a long model key no longer fits VARCHAR(1024). The four secret
-- columns become TEXT.
--
-- Existing values are encrypted by the auth server at startup when the key is set,
-- not here: a migration has no key.

ALTER TABLE channel_integrations ALTER COLUMN bot_token TYPE TEXT;
ALTER TABLE channel_integrations ALTER COLUMN signing_secret TYPE TEXT;
ALTER TABLE agent_triggers ALTER COLUMN signing_secret TYPE TEXT;
ALTER TABLE agents_config ALTER COLUMN model_api_key TYPE TEXT;
