---
title: Upgrading from 1.x to 2.0
summary: What to do before, during and after moving a 1.x installation to 2.0, what the first start changes, and how to go back.
audience: dev
order: 61
covers:
  - shared/sql/migrations/postgresql/V046__workspaces.sql
  - shared/sql/migrations/postgresql/V047__channel_team_unique.sql
  - shared/sql/migrations/postgresql/V048__workspace_management.sql
  - shared/sql/migrations/postgresql/V049__workspace_provider_keys.sql
  - shared/utils/workspaces.py
  - shared/utils/log_projects.py
  - shared/utils/secret_box.py
---

# Upgrading from 1.x to 2.0

2.0 adds workspaces: several organisations in one installation, each with its own
members, agents, budgets, logs and provider keys. An installation upgraded from 1.x
has one workspace, **Default**, which holds everything it had. It keeps the same routes
and answers its clients as 1.x did. What changes for its users is listed under
[What changes](#what-changes).

The upgrade migrates the database, and **there is no way back except a backup**. Read
this page before you start.

## Before you upgrade

1. **Start from 1.6.1.** An older 1.x upgrades too, since the migrations run in
   order, but on 1.6 you can set the secret key of the next step on its own first.
2. **Set `MATE_SECRET_KEY`.** With `MATE_ENV=production` the auth server refuses to
   start without it, unless `MATE_ALLOW_INSECURE_DEFAULTS` is set. Setting it on 1.6
   first keeps the two changes apart: the next 1.6 start encrypts the secrets stored in
   the clear. If it is already set, keep the same key: a secret encrypted with it reads
   as empty without it. See [Secrets at rest](deployment.md#secrets-at-rest).
3. **Check for two Slack integrations on one team.** V047 makes `team_id` unique on
   `channel_integrations` and fails on an installation that has two rows for one Slack
   team. On the 1.6 database:

   ```sql
   SELECT team_id, COUNT(*) FROM channel_integrations
   WHERE team_id IS NOT NULL GROUP BY team_id HAVING COUNT(*) > 1;
   ```

   Delete or edit all but one row of each team it lists, inactive ones too.
4. **Set `MCP_EXPOSED_AGENTS` if you use agents over MCP.** In 1.x it defaulted to
   `chess_mate_root`. In 2.0 an unset value exposes no agent, so name the agents
   explicitly, `chess_mate_root` included if you used it.
5. **Back up** the main database, the session database and the artifact store, as
   [Backups](database-and-migrations.md#backups) describes, with the server stopped.
   Keep `MATE_SECRET_KEY` with the backup, stored apart from it.
6. **If clients depend on the installation, try the upgrade on a copy first.** Restore
   the backup into a separate database, start 2.0 on it and time the first start: that
   is your maintenance window. Then run your clients' main flows against it: the
   OpenAI-compatible API with a personal access token, the widget, and `/run_sse`
   through the proxy. `shared/test/test_client_contract.py` makes these calls in CI.
   Deploy the production instance from a release tag (`ghcr.io/antiv/mate:2.0`), not
   from `main`.

## The first start

Stop 1.6, deploy 2.0 and start it. The auth server does the following, once each, and
logs each step:

- **Migrations V046–V049**, on PostgreSQL, MySQL and SQLite:
  - V046 adds `workspaces`, with workspace 1 "Default", and `workspace_members`. It
    adds `workspace_id` to projects, test cases, budgets, alert rules and personal
    access tokens, set to 1 for every existing row, and a nullable `workspace_id` to
    the four log tables.
  - V047 makes a Slack team unique (see step 3 above).
  - V048 adds `workspace_invitations`, `workspaces.suspended_at` and
    `workspaces.platform_features`, and makes project names unique per workspace
    instead of across the installation. On SQLite it rebuilds `projects`.
  - V049 adds `workspace_provider_keys` and `workspace_settings`.
- **Memberships.** Every user becomes a member of Default: a user with the `admin`
  role as an owner, anyone else as a member. Pending users and widget visitors don't
  become members. Each user's agent roles (everything in `users.roles` but `admin`,
  `pending` and `widget`) move to their Default membership, and `users.roles` keeps
  only those three. The server's own `trigger_runner` keeps its roles. A
  `system_settings` row, `workspaces.roles_moved_to_members`, records that the move is
  done.
- **The log backfill**, beside the server, so that it doesn't delay startup. It sets
  `workspace_id` on the usage, guardrail, response and audit log rows written before
  V046, from their project, in batches. Until it finishes, a row without a workspace
  counts as Default's, so nothing is missing from the dashboard meanwhile.

On a copy of a production PostgreSQL 15 database at V045, the four migrations took
0.03 s, the membership step moved the roles of 6 users, and the backfill set 4,169
log rows in under a second; the server answered within 3 seconds of starting.

## Check the upgrade

- `python shared/migrate.py status` (`docker compose exec mate python shared/migrate.py
  status` in Docker) lists no pending migration. When a migration fails, the server
  logs which one and starts without it; the others are applied. Fix the cause (for
  V047, the duplicate Slack team) and restart, and the next start applies it.
- The log has the line `Workspace members: ... added`, with the number of users whose
  roles moved.
- Sign in as an administrator. **Control Room → Members** lists the users with their
  agent roles, and **Platform → Workspaces** lists Default.
- A personal access token, the widget and `/run_sse` answer as before.

## What changes

For an installation with one workspace:

- **Agent roles are edited on Control Room → Members**, per workspace, and no longer on
  the user. The **Users** page edits only the platform roles `admin` and `pending`.
  See [Users and roles](../user/users-and-roles.md).
- **"Admin" means an administrator of the workspace.** A user with the `admin` role is
  a platform administrator and an owner of Default; owners and admins of other
  workspaces administer only those. Taking `admin` away from a user makes them a
  member of Default instead of an owner.
- **A personal access token reaches one workspace.** Existing tokens reach Default.
  `ALLOWED_API_ROLES` is matched on the user's agent roles in the token's workspace.
- **Responses that list projects, agents or logs** gain a `workspace_id` field. A
  client that refuses unknown fields has to accept it.
- **An RBAC refusal's audit entry** lists, in `user_roles`, the user's agent roles in
  the agent's workspace, where 1.x listed `users.roles`.
- **No agent is exposed over MCP** unless `MCP_EXPOSED_AGENTS` names it.
- **Provider keys and the image model can be set per workspace**, on **Models and
  keys**. Default always uses the keys in the environment as well, so 1.x agents run
  on the same keys. A key is saved only when `MATE_SECRET_KEY` is set. See
  [Workspaces](../user/workspaces.md#models-and-keys).
- **Code-running tools and the server's own credentials** (the code executor, stdio
  MCP servers, hardcoded agents, Google Drive, Calendar, Supabase Storage, the
  server's SMTP account) stay with Default. A platform administrator can allow them
  for another workspace on **Platform → Workspaces**.
- **Single sign-on uses PKCE.** The redirect URIs registered with Google and GitHub
  don't change.

Adding a second workspace is described in [Workspaces](../user/workspaces.md).

## Going back to 1.x

Restore the backup taken before the upgrade, and deploy the 1.6 image on it. Whatever
was written after the upgrade is lost with it.

Don't run 1.x on the migrated database. 1.x reads agent roles from `users.roles`,
which no longer holds them after the first start, so every user would lose access to
the agents their roles opened, and 1.x knows nothing of the other workspaces' rows.
