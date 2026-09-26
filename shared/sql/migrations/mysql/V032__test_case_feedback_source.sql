-- Migration: link an eval test case to the rated-down response it was made from
-- Version: V032
-- Database: MySQL
--
-- A thumbs-down in response_feedback can be turned into a test case from the evals
-- page. source_feedback_id records which one, so the same response is not added
-- twice. No foreign key: a feedback row going away must not touch the test case.

ALTER TABLE test_cases ADD COLUMN IF NOT EXISTS source_feedback_id INTEGER NULL;
