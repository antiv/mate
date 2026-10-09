-- Migration: Alert rules accept new condition and destination types
-- Version: V037
-- Database: PostgreSQL

-- V026 pinned condition_type and destination_type with CHECK constraints, so every
-- new type (model_fallback_count, slack, discord) failed on insert. The API already
-- validates both against alert_service.CONDITION_TYPES and DESTINATION_TYPES, and
-- tables made by create_all never had the checks (hence IF EXISTS), so they are
-- dropped rather than widened.

ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_condition_type_check;
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_destination_type_check;
