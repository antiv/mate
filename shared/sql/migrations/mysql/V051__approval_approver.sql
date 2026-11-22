-- Migration: approvals inbox, phase 3 (who answers, and answering once)
-- Version: V051
-- Database: MySQL
--
-- approver: who may answer the request, from the agent's require_confirmation
-- settings when it was recorded: chat (also NULL, rows from before), inbox or either.
-- A run nobody is chatting in is answered in the inbox whatever it says.
--
-- answered_at: when the answer reached the run. A request is answered once: a
-- second answer, such as a late click on the chat's card after the inbox decided,
-- is not passed on to the agent. Requests decided before this migration count as
-- answered when they were decided.

ALTER TABLE approval_requests ADD COLUMN approver VARCHAR(10);
ALTER TABLE approval_requests ADD COLUMN answered_at DATETIME;
UPDATE approval_requests SET answered_at = decided_at
    WHERE status IN ('approved', 'rejected', 'expired') AND decided_at IS NOT NULL;
