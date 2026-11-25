-- Migration: the model group of a call
-- Version: V054
-- Database: SQLite
--
-- An agent may name a model group instead of a model (shared/utils/model_groups.py).
-- token_usage_logs.model_name stays the model that answered; model_group is the group's
-- name, NULL for an agent that names a model.

ALTER TABLE token_usage_logs ADD COLUMN model_group VARCHAR(40);
