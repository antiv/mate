#!/usr/bin/env python3
"""
Database migration management CLI for MATE (Multi-Agent Tree Engine).

Usage:
    python migrate.py run                    # Run all pending migrations
    python migrate.py status                 # Show migration status
    python migrate.py create <name>          # Create new migration
    python migrate.py rollback <version>     # Rollback specific migration
"""

import sys
import os
import json
from dotenv import load_dotenv

# Add the parent directory to the path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

from .utils.migration_system import MigrationSystem


def main():
    if len(sys.argv) < 2:
        print("Usage: python migrate.py [run|status|create <name>|rollback <version>]")
        sys.exit(1)
    
    command = sys.argv[1]
    migration_system = MigrationSystem()
    
    if command == "run":
        print("Running database migrations...")
        success = migration_system.run_migrations()
        if success:
            print("✅ All migrations completed successfully!")
            sys.exit(0)
        else:
            print("❌ Some migrations failed!")
            sys.exit(1)
    
    elif command == "status":
        print("Migration Status:")
        print("=" * 50)
        status = migration_system.get_migration_status()
        
        if "error" in status:
            print(f"❌ Error: {status['error']}")
            sys.exit(1)
        
        print(f"Applied migrations: {status['applied_migrations']}")
        print(f"Available migrations: {status['available_migrations']}")
        print(f"Pending migrations: {status['pending_migrations']}")
        print(f"Orphaned migrations: {status['orphaned_migrations']}")
        
        if status['applied']:
            print("\n✅ Applied migrations:")
            for migration in status['applied']:
                print(f"  V{migration['version']} - {migration['name']} ({migration['applied_at']})")
        
        if status['pending']:
            print("\n⏳ Pending migrations:")
            for migration in status['pending']:
                print(f"  V{migration['version']} - {migration['name']}")
        
        if status['orphaned']:
            print("\n⚠️  Orphaned migrations (applied but file missing):")
            for version in status['orphaned']:
                print(f"  V{version}")
    
    elif command == "create":
        if len(sys.argv) < 3:
            print("Usage: python migrate.py create <migration_name>")
            print("Example: python migrate.py create add_new_table")
            sys.exit(1)
        
        name = sys.argv[2]
        print(f"Creating migration: {name}")
        
        filepath = migration_system.create_migration(name)
        if filepath:
            print(f"✅ Created migration: {filepath}")
            print("\nEdit the file to add your migration SQL, then run:")
            print("python migrate.py run")
        else:
            print("❌ Failed to create migration")
            sys.exit(1)
    
    elif command == "rollback":
        if len(sys.argv) < 3:
            print("Usage: python migrate.py rollback <version>")
            print("Example: python migrate.py rollback 001")
            sys.exit(1)
        
        version = sys.argv[2]
        print(f"Rolling back migration V{version}...")
        
        success = migration_system.rollback_migration(version)
        if success:
            print(f"✅ Successfully rolled back migration V{version}")
        else:
            print(f"❌ Failed to rollback migration V{version}")
            sys.exit(1)
    
    else:
        print(f"Unknown command: {command}")
        print("Usage: python migrate.py [run|status|create <name>|rollback <version>]")
        sys.exit(1)


if __name__ == "__main__":
    main()
