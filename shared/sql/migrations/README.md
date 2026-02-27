# Database Migrations

This directory contains database migration scripts for the MATE (Multi-Agent Tree Engine) system.

## Migration System

The migration system provides:
- **Version tracking**: Each migration has a unique version number
- **Automatic execution**: Migrations run automatically on server startup
- **Checksum validation**: Detects if migration files have been modified
- **Rollback support**: Optional rollback scripts for each migration
- **Status tracking**: Track which migrations have been applied

## Migration Files

Migration files follow this naming convention:
- `V001__add_users_table.sql` - Migration script
- `R001__add_users_table.sql` - Optional rollback script

### Database-Specific Migrations

The system supports database-specific migration files organized in subdirectories:

```
sql/migrations/
├── postgresql/          # PostgreSQL-specific migrations
│   ├── V001__add_users_table.sql
│   └── V002__add_status_fields.sql
├── sqlite/              # SQLite-specific migrations
│   ├── V001__add_users_table.sql
│   └── V002__add_status_fields.sql
├── mysql/               # MySQL-specific migrations
│   ├── V001__add_users_table.sql
│   └── V002__add_status_fields.sql
└── README.md
```

The system automatically selects the appropriate migration files based on your `DB_TYPE` environment variable.

### File Format

**PostgreSQL:**
```sql
-- Migration: Brief description
-- Version: V001
-- Created: 2024-01-01
-- Database: POSTGRESQL

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'my_table') THEN
        CREATE TABLE my_table (...);
    END IF;
END $$;
```

**SQLite:**
```sql
-- Migration: Brief description
-- Version: V001
-- Created: 2024-01-01
-- Database: SQLITE

CREATE TABLE IF NOT EXISTS my_table (...);
```

**MySQL:**
```sql
-- Migration: Brief description
-- Version: V001
-- Created: 2024-01-01
-- Database: MYSQL

CREATE TABLE IF NOT EXISTS my_table (...);
```

## Usage

### Automatic Migrations (Server Startup)

Migrations run automatically when the server starts. This is controlled by the `DatabaseClient` initialization.

### Manual Migration Management

```bash
# Run all pending migrations
python migrate.py run

# Check migration status
python migrate.py status

# Create a new migration
python migrate.py create add_new_feature

# Rollback a specific migration
python migrate.py rollback 001
```

### Migration Status

The status command shows:
- **Applied migrations**: Already executed migrations
- **Pending migrations**: Available but not yet applied
- **Orphaned migrations**: Applied but file missing

## Creating New Migrations

1. **Create migration file**:
   ```bash
   python migrate.py create add_new_table
   ```

2. **Edit the generated file** with your SQL:
   ```sql
   -- Migration: Add new table
   -- Version: V003
   -- Created: 2024-01-01

   CREATE TABLE IF NOT EXISTS public.new_table (
       id SERIAL PRIMARY KEY,
       name VARCHAR(255) NOT NULL
   );
   ```

3. **Test the migration**:
   ```bash
   python migrate.py run
   ```

4. **Optional: Create rollback script**:
   ```bash
   # Create R003__add_new_table.sql
   DROP TABLE IF EXISTS public.new_table;
   ```

## Migration Best Practices

### Idempotency
Always make migrations idempotent (safe to run multiple times):

```sql
-- Good: Check if exists first
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables 
                   WHERE table_name = 'my_table') THEN
        CREATE TABLE my_table (...);
    END IF;
END $$;

-- Bad: Will fail on second run
CREATE TABLE my_table (...);
```

### Database Compatibility
Use database-agnostic SQL when possible:

```sql
-- Good: Works across databases
ALTER TABLE users ADD COLUMN email VARCHAR(255);

-- PostgreSQL-specific: Use DO $$ blocks
DO $$
BEGIN
    -- PostgreSQL-specific logic here
END $$;
```

### Data Migration
For data changes, use transactions:

```sql
BEGIN;
-- Your data migration SQL here
UPDATE users SET status = 'active' WHERE status IS NULL;
COMMIT;
```

### Testing
- Test migrations on a copy of production data
- Verify rollback scripts work correctly
- Check that migrations are idempotent

## Current Migrations

| Version | Name | Description |
|---------|------|-------------|
| V001 | initial_schema | Consolidated initial schema: projects, users, token_usage_logs, agents_config, memory_*, credentials, file_search_* tables and seed data. Use this single migration for fresh repository setup. |

## Troubleshooting

### Migration Fails
1. Check the error logs
2. Fix the migration SQL
3. Run `python migrate.py run` again

### Orphaned Migrations
If you see orphaned migrations:
1. Check if migration files were moved/deleted
2. Restore the files or clean up the database records

### Checksum Mismatch
If migration files have been modified after application:
1. Review the changes
2. Update the migration record in `schema_migrations` table
3. Or create a new migration for the changes

## Database Schema

The migration system uses a `schema_migrations` table:

```sql
CREATE TABLE schema_migrations (
    id SERIAL PRIMARY KEY,
    version VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum VARCHAR(64)
);
```

This table tracks all applied migrations and their checksums.
