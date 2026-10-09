-- Migration: Question and answer sent with a rating
-- Version: V038
-- Database: SQLite
--
-- A standalone build forwards its ratings to a central MATE, which cannot read the
-- build's sessions. The build sends the rated question and answer with the rating,
-- and they are kept here. Rows from MATE's own chats leave both NULL and are read
-- from the session as before.

ALTER TABLE response_feedback ADD COLUMN question TEXT NULL;
ALTER TABLE response_feedback ADD COLUMN answer TEXT NULL;
