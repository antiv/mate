-- Migration: one integration per Slack team (workspaces, phase 2d)
-- Version: V047
-- Database: MySQL
--
-- An inbound Slack event names its team, and the integration is looked up by team_id
-- (server/slack_routes.py). With two rows for one team, the first one answered every
-- event, whichever workspace it belonged to. A team now connects to one agent.
--
-- Rows without a team_id are left alone: the index takes any number of NULLs.
-- An installation with two rows for one team fails this migration, and the server
-- logs it and starts without it. Find them with
--   SELECT team_id, COUNT(*) FROM channel_integrations
--   WHERE team_id IS NOT NULL GROUP BY team_id HAVING COUNT(*) > 1;
-- delete or edit all but one of each (an inactive one too), and restart.

CREATE UNIQUE INDEX uq_channel_integrations_team_id ON channel_integrations (team_id);
