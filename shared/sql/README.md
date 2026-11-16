# SQL Directory

This directory holds the SQL that shapes the MATE database. The schema is defined by
the ORM models and created by the migrations, so this page is an index, not a copy of
the tables.

## What is here

- `migrations/` — one SQL file per schema change, in one folder per database type:
  `migrations/{postgresql,mysql,sqlite}/V<NNN>__<name>.sql`. See
  [migrations/README.md](./migrations/README.md).
- `migrate.py` — the migration CLI: `python shared/migrate.py run`, `status` and
  `create <name>`.
- `status_queries.sql` — ad-hoc queries for monitoring request statuses, errors and
  access denials in `token_usage_logs`.

## The schema

The authoritative table and column list is the generated
[database reference](../../docs/reference/database.md). It is produced from
`shared/utils/models.py` by `python scripts/gen_docs.py`; do not copy table
definitions into this page. Change the model, add a migration, then regenerate.

## Migrations

Migrations are plain SQL files named `V<NNN>__<name>.sql`. The auth server applies the
pending files of the active `DB_TYPE` on startup, in order, recording each version and
its checksum in the `schema_migrations` table. To manage them by hand:

```bash
python shared/migrate.py status        # applied and pending migrations
python shared/migrate.py run           # apply pending migrations now
python shared/migrate.py create <name> # scaffold the file in all three dialects
```

There is no rollback command: write a new migration that reverses the change, or
restore a backup. The full workflow, the dialect differences and the undo procedure
are in [database and migrations](../../docs/dev/database-and-migrations.md).

## Backups and reset

Back up the main database, the ADK session database and the artifact store. To reset,
restore that backup, or start from an empty database and let the server apply every
migration. See [Backups](../../docs/dev/database-and-migrations.md#backups) and
[Undoing a migration](../../docs/dev/database-and-migrations.md#undoing-a-migration).
