-- Migration: the Slack team's name, read from the bot token (issue #372)
-- Version: V052
-- Database: PostgreSQL
--
-- team_name: the team's name as Slack's auth.test gave it with the team id. NULL in
-- rows from before, whose team id was typed into the form: it is checked when the
-- integration is next edited.

ALTER TABLE channel_integrations ADD COLUMN team_name VARCHAR(255);
