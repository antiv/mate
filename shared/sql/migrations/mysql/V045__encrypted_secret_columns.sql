-- Migration: TEXT for the encrypted secret columns
-- Version: V045
-- Database: MySQL
--
-- Secrets stored encrypted (MATE_SECRET_KEY, shared/utils/secret_box.py) are longer
-- than the plain value: a Fernet token plus its "enc:v1:" prefix is about 1.4 times
-- the secret and at least 107 characters, so a 255-character bot token no longer fits
-- VARCHAR(255) and a long model key no longer fits VARCHAR(1024). The four secret
-- columns become TEXT.
--
-- Existing values are encrypted by the auth server at startup when the key is set,
-- not here: a migration has no key.

ALTER TABLE channel_integrations MODIFY COLUMN bot_token TEXT NULL;
ALTER TABLE channel_integrations MODIFY COLUMN signing_secret TEXT NULL;
ALTER TABLE agent_triggers MODIFY COLUMN signing_secret TEXT NULL;
ALTER TABLE agents_config MODIFY COLUMN model_api_key TEXT NULL;
