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
---

# Database and migrations

MATE runs on SQLite (the default), PostgreSQL or MySQL, selected with `DB_TYPE`.
Connection settings are listed in the
[configuration reference](../reference/configuration.md); the tables are listed in
the [database reference](../reference/database.md).

## Models and migrations are separate

`shared/utils/models.py` holds the SQLAlchemy models the code queries through.
`shared/sql/migrations/` holds the SQL that creates and changes the tables. Nothing
generates one from the other: a schema change is a model edit **and** a migration.

Conventions in the models:

- Structured values are stored as JSON text in a `Text` column, with `get_*` and
  `set_*` helpers on the model (`get_roles`, `set_output_config`, and so on).
- Secrets that must be verified are stored as a hash; ones that must be reused are
  stored in the clear and left out of `to_dict()`.
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
exists" or "duplicate column name" is skipped rather than aborting the migration.
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

1. `python shared/migrate.py create <name>` writes the next numbered file, **only in
   the folder of the database type you are currently running**.
2. Write the SQL.
3. Create the same version, with the same name, in the other two folders, in that
   dialect's SQL. The usual differences:

   | | SQLite | PostgreSQL | MySQL |
   |---|---|---|---|
   | Auto-increment key | `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` | `INT AUTO_INCREMENT PRIMARY KEY` |
   | Boolean | `BOOLEAN` (stored as 0/1) | `BOOLEAN` | `TINYINT(1)` or `BOOLEAN` |
   | Timestamp | `DATETIME` | `TIMESTAMP` | `DATETIME` |
   | Add a column if missing | not available; rely on the tolerant apply | `ADD COLUMN IF NOT EXISTS` | not available; rely on the tolerant apply |

4. Update the model in `models.py`.
5. Run `python scripts/gen_docs.py`. The database reference lists every version
   against the three dialects and marks a missing file with ✗, so a forgotten
   dialect shows up in the diff.
6. Run the tests; `shared/test/` includes migration tests.

Never edit a migration that has been released. Installations have already applied
it, and nothing re-runs a file whose version is recorded. Add a new one.

## Rollback

`python shared/migrate.py rollback` looks for a file named `R<number>__<name>.sql`
and, if it finds one, runs it and removes the version from `schema_migrations`.
Without that file it does nothing. No migration in the repository currently ships a
rollback file, so in practice a migration is undone by writing a new one, or by
restoring a backup.

## Conversations are stored elsewhere

The tables above do not include chat history on the ADK runtime: ADK's session
service creates and owns its own tables, in the database passed to the agent server
as `--session-db-url`. The LangGraph runtime keeps its history in `lg_sessions` and
`lg_events`, which are regular MATE tables, plus a checkpointer database.

## Backups

Back up the main database, the session database, and the artifact store (the
`artifacts/` folder for local storage). With SQLite, both databases are files; copy
them while the server is stopped, or use SQLite's online backup.
