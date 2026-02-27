"""
Database migration system for MATE (Multi-Agent Tree Engine).

Handles database schema migrations with version tracking, automatic execution,
and rollback capabilities.
"""

import os
import logging
import re
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from sqlalchemy import text, create_engine, MetaData, Table, Column, Integer, String, DateTime
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class MigrationRecord:
    """Represents a database migration record."""
    
    def __init__(self, version: str, name: str, applied_at: datetime, checksum: str = ""):
        self.version = version
        self.name = name
        self.applied_at = applied_at
        self.checksum = checksum


class MigrationSystem:
    """Database migration system with version tracking."""
    
    def __init__(self, database_client=None):
        self.database_client = database_client
        self.migrations_dir = os.path.join(os.path.dirname(__file__), '..', 'sql', 'migrations')
        self._ensure_migrations_dir()
        
    def _ensure_migrations_dir(self):
        """Ensure migrations directory exists."""
        if not os.path.exists(self.migrations_dir):
            os.makedirs(self.migrations_dir)
            logger.info(f"Created migrations directory: {self.migrations_dir}")
    
    def _get_engine(self):
        """Get SQLAlchemy engine from database client."""
        if self.database_client:
            return self.database_client._engine
        else:
            # Create engine directly if no client provided
            import os
            from sqlalchemy import create_engine
            
            db_type = os.getenv("DB_TYPE", "postgresql").lower()
            
            if db_type == "postgresql":
                host = os.getenv("DB_HOST", "localhost")
                port = os.getenv("DB_PORT", "5432")
                database = os.getenv("DB_NAME")
                user = os.getenv("DB_USER")
                password = os.getenv("DB_PASSWORD")
                
                if not all([database, user, password]):
                    return None
                
                database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
            elif db_type == "sqlite":
                database_path = os.getenv("DB_PATH", "mate_agent.db")
                database_url = f"sqlite:///{database_path}"
            elif db_type == "mysql":
                host = os.getenv("DB_HOST", "localhost")
                port = os.getenv("DB_PORT", "3306")
                database = os.getenv("DB_NAME")
                user = os.getenv("DB_USER")
                password = os.getenv("DB_PASSWORD")
                
                if not all([database, user, password]):
                    return None
                
                database_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
            else:
                return None
            
            return create_engine(database_url)
    
    def _get_database_type(self):
        """Get database type from engine URL."""
        engine = self._get_engine()
        if not engine:
            return 'unknown'
        
        url = str(engine.url)
        if 'sqlite' in url:
            return 'sqlite'
        elif 'postgresql' in url:
            return 'postgresql'
        elif 'mysql' in url:
            return 'mysql'
        else:
            return 'unknown'
    
    def _clean_sql_for_database_type(self, sql_content: str) -> str:
        """Clean SQL content to work with different database types."""
        db_type = self._get_database_type()
        
        if db_type == 'sqlite':
            # Convert PostgreSQL-specific syntax to SQLite-compatible
            import re
            
            # Handle DO $$ blocks - convert to simple SQLite statements
            sql_content = self._convert_postgresql_to_sqlite(sql_content)
            
        elif db_type == 'mysql':
            # Convert PostgreSQL-specific syntax to MySQL-compatible
            import re
            sql_content = self._convert_postgresql_to_mysql(sql_content)
        
        # For PostgreSQL, return as-is
        return sql_content
    
    def _convert_postgresql_to_sqlite(self, sql_content: str) -> str:
        """Convert PostgreSQL-specific syntax to SQLite-compatible SQL."""
        import re
        
        # Remove DO $$ blocks and extract the actual SQL
        do_blocks = re.findall(r'DO \$\$\s*BEGIN\s*(.*?)END \$\$;', sql_content, flags=re.DOTALL | re.IGNORECASE)
        
        converted_sql = ""
        
        for block in do_blocks:
            # Remove RAISE NOTICE statements
            block = re.sub(r'RAISE NOTICE .*?;', '', block, flags=re.IGNORECASE)
            
            # Remove COMMENT ON statements (SQLite doesn't support them)
            block = re.sub(r'COMMENT ON .*?;', '', block, flags=re.IGNORECASE)
            
            # Remove schema prefixes
            block = block.replace('public.', '')
            
            # Replace SERIAL with INTEGER PRIMARY KEY AUTOINCREMENT
            block = re.sub(r'SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT', block, flags=re.IGNORECASE)
            
            # Convert IF NOT EXISTS checks to simple CREATE IF NOT EXISTS
            # This is a simplified approach - for complex logic, you'd need database-specific files
            if 'CREATE TABLE' in block.upper():
                block = re.sub(r'CREATE TABLE\s+(\w+)', r'CREATE TABLE IF NOT EXISTS \1', block, flags=re.IGNORECASE)
            
            converted_sql += block + "\n"
        
        # If no DO blocks found, clean the original content
        if not do_blocks:
            converted_sql = sql_content
            # Remove RAISE NOTICE statements
            converted_sql = re.sub(r'RAISE NOTICE .*?;', '', converted_sql, flags=re.IGNORECASE)
            # Remove COMMENT ON statements
            converted_sql = re.sub(r'COMMENT ON .*?;', '', converted_sql, flags=re.IGNORECASE)
            # Remove schema prefixes
            converted_sql = converted_sql.replace('public.', '')
            # Replace SERIAL with INTEGER PRIMARY KEY AUTOINCREMENT
            converted_sql = re.sub(r'SERIAL PRIMARY KEY', 'INTEGER PRIMARY KEY AUTOINCREMENT', converted_sql, flags=re.IGNORECASE)
        
        return converted_sql
    
    def _convert_postgresql_to_mysql(self, sql_content: str) -> str:
        """Convert PostgreSQL-specific syntax to MySQL-compatible SQL."""
        import re
        
        # Remove DO $$ blocks (MySQL uses different syntax)
        sql_content = re.sub(r'DO \$\$\s*BEGIN\s*.*?END \$\$;', '', sql_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove RAISE NOTICE statements
        sql_content = re.sub(r'RAISE NOTICE .*?;', '', sql_content, flags=re.IGNORECASE)
        
        # Remove schema prefixes
        sql_content = sql_content.replace('public.', '')
        
        # Replace SERIAL with AUTO_INCREMENT
        sql_content = re.sub(r'SERIAL PRIMARY KEY', 'INT AUTO_INCREMENT PRIMARY KEY', sql_content, flags=re.IGNORECASE)
        
        return sql_content
    
    def _split_sql_statements(self, sql_content: str) -> list:
        """Split SQL content into individual statements."""
        import re
        
        # For PostgreSQL DO $$ blocks, don't split them
        if 'DO $$' in sql_content.upper():
            # Return the entire content as a single statement
            return [sql_content.strip()]
        
        # Remove comments (both -- and /* */ style)
        sql_content = re.sub(r'--.*$', '', sql_content, flags=re.MULTILINE)
        sql_content = re.sub(r'/\*.*?\*/', '', sql_content, flags=re.DOTALL)
        
        # Split by semicolon, but be careful with semicolons inside strings
        statements = []
        current_statement = ""
        in_string = False
        string_char = None
        
        for char in sql_content:
            if char in ["'", '"'] and not in_string:
                in_string = True
                string_char = char
                current_statement += char
            elif char == string_char and in_string:
                in_string = False
                string_char = None
                current_statement += char
            elif char == ';' and not in_string:
                current_statement = current_statement.strip()
                if current_statement:
                    statements.append(current_statement)
                current_statement = ""
            else:
                current_statement += char
        
        # Add the last statement if it doesn't end with semicolon
        current_statement = current_statement.strip()
        if current_statement:
            statements.append(current_statement)
        
        return statements
    
    def _create_migrations_table(self, engine):
        """Create the schema_migrations table if it doesn't exist."""
        metadata = MetaData()
        
        migrations_table = Table(
            'schema_migrations',
            metadata,
            Column('id', Integer, primary_key=True),
            Column('version', String(20), unique=True, nullable=False),
            Column('name', String(255), nullable=False),
            Column('applied_at', DateTime, nullable=False, default=datetime.utcnow),
            Column('checksum', String(64), nullable=True)
        )
        
        try:
            metadata.create_all(engine)
            logger.info("Schema migrations table created/verified")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Failed to create migrations table: {e}")
            return False
    
    def _get_applied_migrations(self, engine) -> List[MigrationRecord]:
        """Get list of already applied migrations."""
        try:
            with engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT version, name, applied_at, checksum 
                    FROM schema_migrations 
                    ORDER BY version
                """))
                
                migrations = []
                for row in result:
                    migrations.append(MigrationRecord(
                        version=row[0],
                        name=row[1],
                        applied_at=row[2],
                        checksum=row[3] or ""
                    ))
                
                return migrations
                
        except SQLAlchemyError as e:
            logger.warning(f"Failed to get applied migrations: {e}")
            return []
    
    def _get_migration_files(self) -> List[Tuple[str, str, str]]:
        """Get list of migration files (version, name, filepath)."""
        db_type = self._get_database_type()
        
        # Try database-specific directory first
        db_specific_dir = os.path.join(self.migrations_dir, db_type)
        if os.path.exists(db_specific_dir):
            return self._get_migration_files_from_dir(db_specific_dir)
        
        # Fall back to general migrations directory
        if os.path.exists(self.migrations_dir):
            return self._get_migration_files_from_dir(self.migrations_dir)
        
        return []
    
    def _get_migration_files_from_dir(self, directory: str) -> List[Tuple[str, str, str]]:
        """Get migration files from a specific directory."""
        migration_files = []
        
        for filename in os.listdir(directory):
            if filename.endswith('.sql'):
                # Expected format: V001__add_users_table.sql
                match = re.match(r'V(\d+)__(.+)\.sql', filename)
                if match:
                    version = match.group(1)
                    name = match.group(2)
                    filepath = os.path.join(directory, filename)
                    migration_files.append((version, name, filepath))
        
        # Sort by version number (integer) instead of string
        migration_files.sort(key=lambda x: int(x[0]))
        
        return migration_files
    
    def _calculate_file_checksum(self, filepath: str) -> str:
        """Calculate checksum for migration file."""
        import hashlib
        
        with open(filepath, 'rb') as f:
            content = f.read()
            return hashlib.md5(content).hexdigest()
    
    def _apply_migration(self, engine, version: str, name: str, filepath: str) -> bool:
        """Apply a single migration."""
        try:
            # Read migration file
            with open(filepath, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            
            # Clean SQL for database type
            sql_content = self._clean_sql_for_database_type(sql_content)
            
            # Calculate checksum
            checksum = self._calculate_file_checksum(filepath)
            
            # Execute migration in transaction
            with engine.connect() as conn:
                trans = conn.begin()
                try:
                    # Split SQL into individual statements for SQLite compatibility
                    statements = self._split_sql_statements(sql_content)
                    
                    # Execute each statement separately
                    for statement in statements:
                        if statement.strip():
                            try:
                                conn.execute(text(statement))
                            except SQLAlchemyError as e:
                                # Log the error but continue with other statements
                                # This allows migrations to be more resilient to schema changes
                                logger.warning(f"Statement failed (may be expected): {statement[:100]}... Error: {e}")
                                # For certain expected errors (like duplicate columns), continue
                                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                                    logger.info(f"Skipping statement due to expected error: {e}")
                                    continue
                                else:
                                    # Re-raise unexpected errors
                                    raise
                    
                    # Record the migration
                    conn.execute(text("""
                        INSERT INTO schema_migrations (version, name, applied_at, checksum)
                        VALUES (:version, :name, :applied_at, :checksum)
                    """), {
                        'version': version,
                        'name': name,
                        'applied_at': datetime.utcnow(),
                        'checksum': checksum
                    })
                    
                    trans.commit()
                    logger.info(f"Applied migration V{version}__{name}")
                    return True
                    
                except Exception as e:
                    trans.rollback()
                    logger.error(f"Failed to apply migration V{version}__{name}: {e}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error applying migration V{version}__{name}: {e}")
            return False
    
    def _rollback_migration(self, engine, version: str, name: str) -> bool:
        """Rollback a single migration (if rollback SQL exists)."""
        rollback_file = os.path.join(self.migrations_dir, f'R{version}__{name}.sql')
        
        if not os.path.exists(rollback_file):
            logger.warning(f"No rollback file found for V{version}__{name}")
            return False
        
        try:
            with open(rollback_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()
            
            with engine.connect() as conn:
                trans = conn.begin()
                try:
                    # Execute rollback SQL
                    conn.execute(text(sql_content))
                    
                    # Remove migration record
                    conn.execute(text("""
                        DELETE FROM schema_migrations WHERE version = :version
                    """), {'version': version})
                    
                    trans.commit()
                    logger.info(f"Rolled back migration V{version}__{name}")
                    return True
                    
                except Exception as e:
                    trans.rollback()
                    logger.error(f"Failed to rollback migration V{version}__{name}: {e}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error rolling back migration V{version}__{name}: {e}")
            return False
    
    def run_migrations(self) -> bool:
        """Run all pending migrations."""
        engine = self._get_engine()
        if not engine:
            logger.error("No database engine available")
            return False
        
        # Create migrations table
        if not self._create_migrations_table(engine):
            return False
        
        # Get applied migrations
        applied_migrations = {m.version: m for m in self._get_applied_migrations(engine)}
        
        # Get available migration files
        migration_files = self._get_migration_files()
        
        if not migration_files:
            logger.info("No migration files found")
            return True
        
        # Apply pending migrations
        applied_count = 0
        failed_migrations = []
        
        for version, name, filepath in migration_files:
            if version not in applied_migrations:
                logger.info(f"Applying migration V{version}__{name}")
                if self._apply_migration(engine, version, name, filepath):
                    applied_count += 1
                else:
                    failed_migrations.append(f"V{version}__{name}")
            else:
                # Skip already applied migrations - no need to check checksums
                logger.debug(f"Migration V{version}__{name} already applied - skipping")
        
        if failed_migrations:
            logger.error(f"Failed migrations: {', '.join(failed_migrations)}")
            return False
        
        if applied_count > 0:
            logger.info(f"Successfully applied {applied_count} migrations")
        else:
            logger.info("No pending migrations to apply")
        
        return True
    
    def rollback_migration(self, version: str) -> bool:
        """Rollback a specific migration."""
        engine = self._get_engine()
        if not engine:
            logger.error("No database engine available")
            return False
        
        # Get migration info
        applied_migrations = {m.version: m for m in self._get_applied_migrations(engine)}
        
        if version not in applied_migrations:
            logger.error(f"Migration V{version} not found in applied migrations")
            return False
        
        migration = applied_migrations[version]
        return self._rollback_migration(engine, version, migration.name)
    
    def get_migration_status(self) -> Dict[str, any]:
        """Get current migration status."""
        engine = self._get_engine()
        if not engine:
            return {"error": "No database engine available"}
        
        applied_migrations = self._get_applied_migrations(engine)
        migration_files = self._get_migration_files()
        
        applied_versions = {m.version for m in applied_migrations}
        available_versions = {version for version, _, _ in migration_files}
        
        pending_versions = available_versions - applied_versions
        orphaned_versions = applied_versions - available_versions
        
        return {
            "applied_migrations": len(applied_migrations),
            "available_migrations": len(migration_files),
            "pending_migrations": len(pending_versions),
            "orphaned_migrations": len(orphaned_versions),
            "applied": [{"version": m.version, "name": m.name, "applied_at": m.applied_at} for m in applied_migrations],
            "pending": [{"version": v, "name": n} for v, n, _ in migration_files if v in pending_versions],
            "orphaned": list(orphaned_versions)
        }
    
    def create_migration(self, name: str) -> Optional[str]:
        """Create a new migration file."""
        # Get next version number
        migration_files = self._get_migration_files()
        if migration_files:
            last_version = int(migration_files[-1][0])
            next_version = str(last_version + 1).zfill(3)
        else:
            next_version = "001"
        
        db_type = self._get_database_type()
        
        # Create database-specific directory if it doesn't exist
        db_specific_dir = os.path.join(self.migrations_dir, db_type)
        if not os.path.exists(db_specific_dir):
            os.makedirs(db_specific_dir)
        
        filename = f"V{next_version}__{name}.sql"
        filepath = os.path.join(db_specific_dir, filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"-- Migration: {name}\n")
                f.write(f"-- Version: V{next_version}\n")
                f.write(f"-- Created: {datetime.utcnow().isoformat()}\n")
                f.write(f"-- Database: {db_type.upper()}\n\n")
                f.write("-- Add your migration SQL here\n")
            
            logger.info(f"Created migration file: {filename} for {db_type}")
            return filepath
            
        except Exception as e:
            logger.error(f"Failed to create migration file: {e}")
            return None


def run_migrations_on_startup() -> bool:
    """Run migrations automatically on application startup."""
    try:
        migration_system = MigrationSystem()
        return migration_system.run_migrations()
    except Exception as e:
        logger.error(f"Failed to run migrations on startup: {e}")
        return False


if __name__ == "__main__":
    # CLI for migration management
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python migration_system.py [run|status|create <name>|rollback <version>]")
        sys.exit(1)
    
    command = sys.argv[1]
    migration_system = MigrationSystem()
    
    if command == "run":
        success = migration_system.run_migrations()
        sys.exit(0 if success else 1)
    
    elif command == "status":
        status = migration_system.get_migration_status()
        print(json.dumps(status, indent=2, default=str))
    
    elif command == "create":
        if len(sys.argv) < 3:
            print("Usage: python migration_system.py create <migration_name>")
            sys.exit(1)
        name = sys.argv[2]
        filepath = migration_system.create_migration(name)
        if filepath:
            print(f"Created migration: {filepath}")
        else:
            sys.exit(1)
    
    elif command == "rollback":
        if len(sys.argv) < 3:
            print("Usage: python migration_system.py rollback <version>")
            sys.exit(1)
        version = sys.argv[2]
        success = migration_system.rollback_migration(version)
        sys.exit(0 if success else 1)
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
