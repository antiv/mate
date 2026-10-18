-- Migration: Feedback key for standalone builds
-- Version: V039
-- Database: MySQL
--
-- A standalone build forwards its ratings with the rated question and answer,
-- which this server cannot check against a session. Only a holder of this key may
-- send them: the public api_key is in every page that embeds the widget. The key
-- goes in the build's .env (MATE_FEEDBACK_KEY), never in a web page.

ALTER TABLE widget_api_keys ADD COLUMN feedback_key VARCHAR(255);

UPDATE widget_api_keys
SET feedback_key = CONCAT('wfk_', SHA2(CONCAT(RAND(), UUID(), id), 256))
WHERE feedback_key IS NULL;

CREATE UNIQUE INDEX ix_widget_api_keys_feedback_key ON widget_api_keys (feedback_key);
