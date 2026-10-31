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
  project's, so an entry about a project that no longer exists has none.

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
Phase 1 (V046) adds the schema only, and nothing reads it yet:

- **New tables.**
  - `workspaces` holds workspace 1, "Default", which V046 inserts. A schema built
    by `create_all` gets it too, from an `after_create` hook on the table.
  - `workspace_members` holds one row per workspace and user, with `role` (`owner`,
    `admin` or `member`) and `agent_roles`.
- **Existing tables.** `projects`, `test_cases`, `rate_limit_config`, `alert_rules`
  and `personal_access_tokens` have `workspace_id NOT NULL DEFAULT 1`.
  - The default puts existing rows in workspace 1.
  - Code that doesn't know a workspace yet writes `models.DEFAULT_WORKSPACE_ID`.
  - Only PostgreSQL and MySQL have a foreign key from `projects.workspace_id`.
    SQLite can't add a `REFERENCES` column with a default by `ALTER TABLE`.
- **Members are derived from `users.roles` until a later phase edits them on their
  own** (`shared/utils/workspaces.py`).
  - A user with the `admin` role is an `owner`; anyone else is a `member`.
  - `agent_roles` are the user's roles without `admin`.
  - A user with the `widget` or `pending` role is no member. A widget reaches its
    workspace through its key's project. Approving a pending user replaces
    `pending`, which adds the membership.
  - The auth server runs `sync_default_members()` at startup, before it starts the
    agent server. That adds the users of a database that has just migrated. It
    reads users and members as plain rows and writes only what differs, since
    widget visitors can make `users` a large table.
  - Listeners on the `User` model keep a member in step when a user is inserted or
    their roles change, in whichever process writes it.
  - Deleting a user deletes their memberships (`ON DELETE CASCADE`).

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
