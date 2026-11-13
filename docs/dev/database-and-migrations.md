---
title: Database and migrations
summary: The three supported databases, how the schema is migrated, and how to add a migration without breaking a dialect.
audience: dev
order: 60
covers:
  - shared/utils/migration_system.py
  - shared/migrate.py
  - shared/utils/database_client.py
  - shared/utils/models.py
  - shared/sql/README.md
  - shared/utils/log_projects.py
  - shared/utils/workspaces.py
---

# Database and migrations

MATE runs on SQLite (the default), PostgreSQL or MySQL, selected with `DB_TYPE`.
Connection settings are listed in the
[configuration reference](../reference/configuration.md); the tables are listed in
the [database reference](../reference/database.md).

Every part of MATE reads those settings through `shared/utils/settings.py`: the
database client, the migration runner (also `shared/migrate.py` run on its own), the
ADK session store and the dashboard's database panel. A relative `DB_PATH` is taken
from the project root, and `DB_PORT` defaults to 5432, or 3306 when `DB_TYPE=mysql`.
Read a database variable through that module rather than `os.getenv`, so a new
caller cannot quietly pick a different database.

## Models and migrations are separate

`shared/utils/models.py` holds the SQLAlchemy models the code queries through.
`shared/sql/migrations/` holds the SQL that creates and changes the tables. Nothing
generates one from the other: a schema change is a model edit **and** a migration.

Conventions in the models:

- Structured values are stored as JSON text in a `Text` column, with `get_*` and
  `set_*` helpers on the model (`get_roles`, `set_output_config`, and so on).
- Secrets that must be verified are stored as a hash; ones that must be reused use
  the `EncryptedText` column type, encrypted with `MATE_SECRET_KEY` when it is set
  ([Deployment](deployment.md#secrets-at-rest)), and are left out of `to_dict()`.
- A trailing comment on a column line becomes its note in the generated reference.

## How migrations run

Migrations are plain SQL files named `V<number>__<name>.sql`, in one folder per
database type:

```
shared/sql/migrations/
  sqlite/       V001__initial_schema.sql ... 
  postgresql/   V001__initial_schema.sql ...
  mysql/        V001__initial_schema.sql ...
```

At startup the auth server applies every file in the active type's folder whose
version is not yet in the `schema_migrations` table, in order. Each file runs in one
transaction and is recorded with a checksum.

Statements are executed one at a time, and a statement that fails with "already
exists", "duplicate column name" or "duplicate key name" (MySQL's error for an index
that exists) is skipped rather than aborting the migration.
This makes re-running tolerant, and it also means a typo that happens to produce
one of those errors passes silently. Check the result with `status`.

```bash
python shared/migrate.py status      # applied and pending migrations
python shared/migrate.py run         # apply pending migrations now
python shared/migrate.py create add_widget_theme
```

The **Migrations** page in the Control Room shows the same status and runs pending
migrations. It can also delete a migration's record, or re-run one migration by
deleting its record and applying it again; the tolerant apply described above is
what makes a re-run of `CREATE TABLE` or `ADD COLUMN` statements harmless.

## Add a migration

1. `python shared/migrate.py create <name>` writes the next numbered file into all
   three folders (`sqlite/`, `postgresql/`, `mysql/`), each with a header naming its
   database. The number follows the highest version in any of the folders, whatever
   database you are running.
2. Write the SQL in each file, in that dialect. The usual differences:

   | | SQLite | PostgreSQL | MySQL |
   |---|---|---|---|
   | Auto-increment key | `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` | `INT AUTO_INCREMENT PRIMARY KEY` |
   | Boolean | `BOOLEAN` (stored as 0/1) | `BOOLEAN` | `TINYINT(1)` or `BOOLEAN` |
   | Timestamp | `DATETIME` | `TIMESTAMP` | `DATETIME` |
   | Add a column if missing | not available; rely on the tolerant apply | `ADD COLUMN IF NOT EXISTS` | not available; rely on the tolerant apply |
   | Create an index if missing | `CREATE INDEX IF NOT EXISTS` | `CREATE INDEX IF NOT EXISTS` | not available; rely on the tolerant apply |
   | Default on a `TEXT` column | `DEFAULT 'x'` | `DEFAULT 'x'` | only as an expression: `DEFAULT ('x')` |
   | Index on long strings | no limit | no limit | at most 3072 bytes a key, 4 bytes a character: index a prefix, `col(255)` |

   `shared/test/test_migration_system.py` checks the MySQL files for
   `ADD COLUMN IF NOT EXISTS` and for a `TEXT` default that is not an expression.
   Nothing runs them on MySQL in CI, so try a new MySQL migration on a fresh MySQL 8
   database.

3. Update the model in `models.py`.
4. Run `python scripts/gen_docs.py`. The database reference lists every version
   against the three dialects and marks a missing file with ✗.
   `python scripts/gen_docs.py --check`, which CI runs, fails while any version is
   missing from a dialect.
5. Run the tests; `shared/test/` includes migration tests.

SQLite can't drop a constraint in place, so a migration that changes one rebuilds the
table: create `<table>_vNNN`, copy the rows, `DROP TABLE`, rename (V037, V048). With
foreign keys on, as the database client's connections have them, that `DROP TABLE`
would first delete every row and cascade to the child tables. The runner turns
foreign keys off on a SQLite connection for each migration and back on after it
(`MigrationSystem._sqlite_foreign_keys_off`), as SQLite's own procedure does: the
pragma does nothing inside a transaction. Child tables keep their `REFERENCES`,
which the renamed table answers.

Never edit a migration that has been released. Installations have already applied
it, and nothing re-runs a file whose version is recorded. Add a new one.

A migration that inserts rows (a demo project, a built-in agent) must work on tables
that SQLAlchemy's `create_all` made, as some installations have. Those tables have no
database-side defaults, since the defaults live in the models, and on such an
installation the insert may run for the first time over rows an admin has edited:

- Give every `NOT NULL` column a value, timestamps included (`CURRENT_TIMESTAMP`).
- Insert only when absent (`ON CONFLICT DO NOTHING`, `INSERT IGNORE`,
  `INSERT OR IGNORE`); never upsert over an existing row.
- SQLite's `INSERT OR IGNORE` also skips a row that breaks a `NOT NULL` constraint,
  without an error, so test a seed on a `create_all` schema
  (`shared/test/test_mate_help_agent.py` does this for V036).

## Log rows carry their project and workspace

`token_usage_logs`, `guardrail_logs`, `agent_responses` and `audit_logs` have a
`project_id` (V044) and a `workspace_id` (V046). The four functions that write the
rows set both, and `shared/utils/log_projects.py` does the lookup:

- token usage, guardrail hits and responses get their agent's project and that
  project's workspace, or NULL for an agent that is not configured;
- an audit entry gets the project of the agent or project its `resource_id` names,
  NULL for installation-wide entries. `audit_service.log(..., project_id=...)` sets
  it when the agent is already gone, as on agent delete. The workspace is the
  project's; `workspace_id=...` sets it when the project is gone, as on project
  delete.

The column has no foreign key: a row keeps its project id after the project is
deleted. Queries per project read it rather than joining through `agents_config`,
so a row stays with the project it happened in. The Usage page does this.

Rows written before V044 are filled in by `log_projects.backfill()`, from each
agent's current project. `python auth_server.py` runs it once in a background thread
after startup: it walks each table by id, 10,000 rows per `UPDATE` and commit, and
writes `log_project_backfill = done` to `system_settings` when it is finished. A
failed run logs an error and runs again on the next start. It only writes rows
without a project, so running it twice, or on two servers at once, is harmless.

`log_projects.backfill_workspaces()` then fills `workspace_id` on rows written before
V046, from the project in their `project_id`, in the same thread and the same way. It
marks itself done with `log_workspace_backfill`, and it doesn't start until the
project backfill is done, since it reads that backfill's result. A row with no
project, or whose project was deleted, keeps NULL.

## Workspaces

2.0 lets several organisations share one installation (`documents/design/workspaces.md`).
Phase 1 (V046) adds the schema. Phase 2 scopes requests by it, starting with the
request context (`docs/dev/auth-and-access.md`, "The workspace of a request"):

- **New tables.**
  - `workspaces` holds workspace 1, "Default", which V046 inserts. A schema built
    by `create_all` gets it too, from an `after_create` hook on the table.
  - `workspace_members` holds one row per workspace and user, with `role` (`owner`,
    `admin` or `member`) and `agent_roles`.
- **Existing tables.** `projects`, `test_cases`, `rate_limit_config`, `alert_rules`
  and `personal_access_tokens` have `workspace_id NOT NULL DEFAULT 1`.
  - The default puts existing rows in workspace 1.
  - Code that doesn't know a workspace yet writes `models.DEFAULT_WORKSPACE_ID`.
  - `projects.workspace_id` has a foreign key on PostgreSQL and MySQL since V046,
    and on SQLite since V048 rebuilt the table: SQLite can't add a `REFERENCES`
    column with a default by `ALTER TABLE`.
- **Phase 3 (V048).**
  - `projects.name` is unique per workspace (`uq_projects_workspace_name`), not
    across the installation. A lookup of a project by name takes the workspace.
  - `workspace_invitations`: one email, one workspace, a role. Only the token's
    SHA-256 is stored, and `accepted_at` makes a row used.
  - `workspaces.suspended_at` stops a workspace until it is resumed; `deleted_at`
    closes one.
  - `workspaces.platform_features` is a JSON list of `models.PLATFORM_FEATURES`
    (`code`, `server_credentials`, `email`). The default workspace has all three,
    which is what 1.x does; another workspace starts with none.
  - Both columns are read as `docs/dev/auth-and-access.md` describes ("A closed or
    suspended workspace", and the platform-only features after it).
- **A membership holds the user's `role` and `agent_roles` in its workspace**, edited
  on the Members page (`server/member_routes.py`). `users.roles` keeps the platform
  roles (`workspaces.PLATFORM_ROLES`: `admin`, `pending`, `widget`). The server's own
  callers (`SYSTEM_CALLERS`, `trigger_runner`) belong to no workspace and keep their
  agent roles there. What is left of phase 2's derivation from `users.roles`
  (`shared/utils/workspaces.py`):
  - A user inserted with agent roles in `users.roles`, as the Users page, 1.x code
    and `get_or_create_user` (`["user"]`) write them, becomes a member of their home
    workspace with those agent roles, and the listener leaves `users.roles` the
    platform ones. `admin` makes them an `owner`, anything else a `member`. The home
    workspace is the default one, or the one `get_or_create_user(user_id,
    workspace_id)` names for a user the platform creates for an agent of another
    workspace (`home_workspace_id`).
  - A user with the `widget` or `pending` role is no member. A widget reaches its
    workspace through its key's project. A role change to `pending` or `widget`
    removes every membership of the user. Approving a pending user who is a member
    nowhere adds them to the default workspace, with the agent roles the change
    wrote or `["user"]`.
  - Agent roles written to `users.roles` later move to the user's membership of the
    default workspace; a user who is a member of another workspace only joins it, as a
    `member`, for them. Losing `admin` makes an `owner` of the default workspace a
    `member`, since 1.x's admins became its owners, even its last owner: taking a
    platform administrator's rights away comes first, and platform administrators
    manage the default workspace. Owners of other workspaces stay.
    Any other change of platform roles leaves the memberships alone.
  - The auth server runs `move_roles_to_members()` at startup, before it starts the
    agent server. The first time, it derives the default workspace's memberships as
    phase 2 did, which also adds the users of a database that has just migrated
    from 1.x, and leaves `users.roles` the platform roles; the
    `workspaces.roles_moved_to_members` row of `system_settings` records that it
    ran. Every start it removes the memberships of `pending` and `widget` users. It
    reads users and members as plain rows, since widget visitors can make `users` a
    large table.
  - Deleting a user deletes their memberships (`ON DELETE CASCADE`).
- `test_cases.workspace_id` is the workspace of the case's agent, set when the
  dashboard creates the case. The other lookups in `workspaces.py`
  (`config_workspace`, `version_workspace`, `store_workspace`, `agents_outside`)
  find a row's workspace through its project.
- Queries keep to a workspace with `rows_of(model, workspace_id)`, on the table's
  `workspace_id`, and `in_projects_of(column, workspace_id)`, on a `project_id`. A
  log row with no workspace, including one the backfill hasn't reached, counts as the
  default workspace's, so a 1.x installation sees all of its rows.
- `channel_integrations.team_id` is unique (V047), as an inbound Slack event is
  looked up by its team. NULLs are allowed. On a database that has two rows for one
  team the migration fails: the server logs it, starts without it, and tries again
  on the next start. The migration file has the query that finds them.

## Undoing a migration

There is no rollback command. To undo a migration that has been applied, write a new
migration that reverses it, or restore a backup (see below).
`python shared/migrate.py rollback` only prints this advice and exits with an error.

## Conversations are stored elsewhere

The tables above do not include chat history on the ADK runtime: ADK's session
service creates and owns its own tables, in the database passed to the agent server
as `--session-db-url`. The LangGraph runtime keeps its history in `lg_sessions` and
`lg_events`, which are regular MATE tables, plus a checkpointer database.

## Backups

Back up the main database, the session database, and the artifact store (the
`artifacts/` folder for local storage). With SQLite, both databases are files; copy
them while the server is stopped, or use SQLite's online backup.
