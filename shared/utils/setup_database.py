"""
Database setup script for creating the required tables using SQLAlchemy.

This script creates all tables defined in the models for all supported database types.
"""

import logging
import sys
import os
from dotenv import load_dotenv

# Add the parent directory to the path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from utils.database_client import get_database_client
except ImportError:
    # Fallback for when running as script
    from database_client import get_database_client

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

def create_tables():
    """Create all tables defined in the models."""
    db_client = get_database_client()
    
    if not db_client.is_connected():
        logger.error("Database client not connected")
        return False
    
    try:
        success = db_client.create_tables()
        if success:
            logger.info("All database tables created successfully")
            return True
        else:
            logger.error("Failed to create database tables")
            return False
        
    except Exception as e:
        logger.error(f"Failed to create tables: {e}")
        return False

def main():
    """Main function to set up the database."""
    print("Setting up database with SQLAlchemy...")
    
    if create_tables():
        print("✅ Database setup completed successfully!")
    else:
        print("❌ Database setup failed. Check the logs for details.")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
