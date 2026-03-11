"""
SQLAlchemy database client utility for database operations.

This module provides a centralized way to connect to and interact with different database types
using SQLAlchemy ORM:
- PostgreSQL
- SQLite  
- MySQL
"""

import os
import logging
from typing import Optional
from pathlib import Path
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
import sqlalchemy.pool
from dotenv import load_dotenv
from .models import Base

logger = logging.getLogger(__name__)

# Load environment variables from the project root
def _load_env_file():
    """Load .env file from the project root directory."""
    # Find the project root (where .env file should be)
    current_dir = Path(__file__).parent
    project_root = current_dir.parent.parent  # Go up from shared/utils/ to project root
    
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        logger.info(f"Loaded environment from {env_file}")
    else:
        logger.info("No .env file found, using system environment variables")

# Load environment variables
_load_env_file()


class DatabaseClient:
    """SQLAlchemy database client for multiple database types."""
    
    def __init__(self):
        self._engine = None
        self._session_factory = None
        self._db_type = os.getenv("DB_TYPE", "sqlite").lower()
        self._initialize_connection()
    
    def _get_database_url(self) -> str:
        """Generate database URL based on DB_TYPE."""
        db_type = self._db_type
        
        if db_type == "postgresql":
            host = os.getenv("DB_HOST", "localhost")
            port = os.getenv("DB_PORT", "5432")
            database = os.getenv("DB_NAME")
            user = os.getenv("DB_USER")
            password = os.getenv("DB_PASSWORD")
            
            if not all([database, user, password]):
                raise ValueError("PostgreSQL requires DB_NAME, DB_USER, and DB_PASSWORD")
            
            return f"postgresql://{user}:{password}@{host}:{port}/{database}"
        
        elif db_type == "sqlite":
            database_path = os.getenv("DB_PATH", "my_agent_data.db")
            # Make path absolute to avoid issues when running from different directories
            if not os.path.isabs(database_path):
                # Find the project root (where .env file should be)
                current_dir = Path(__file__).parent
                project_root = current_dir.parent.parent  # Go up from shared/utils/ to project root
                database_path = str(project_root / database_path)
            return f"sqlite:///{database_path}"
        
        elif db_type == "mysql":
            host = os.getenv("DB_HOST", "localhost")
            port = os.getenv("DB_PORT", "3306")
            database = os.getenv("DB_NAME")
            user = os.getenv("DB_USER")
            password = os.getenv("DB_PASSWORD")
            
            if not all([database, user, password]):
                raise ValueError("MySQL requires DB_NAME, DB_USER, and DB_PASSWORD")
            
            return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
        
        else:
            raise ValueError(f"Unsupported database type: {db_type}")
    
    def _initialize_connection(self):
        """Initialize the SQLAlchemy engine and session factory."""
        try:
            database_url = self._get_database_url()
            
            # Create engine with appropriate settings
            engine_kwargs = {
                "echo": False,  # Set to True for SQL query logging
            }
            
            # Add database-specific engine options
            if self._db_type == "sqlite":
                engine_kwargs["connect_args"] = {
                    "check_same_thread": False,
                    "timeout": 30,  # 30 second timeout for busy database
                }
                # Enable connection pooling for SQLite
                engine_kwargs["poolclass"] = sqlalchemy.pool.StaticPool
                engine_kwargs["pool_pre_ping"] = True
            
            self._engine = create_engine(database_url, **engine_kwargs)
            self._session_factory = sessionmaker(bind=self._engine)
            
            # Configure SQLite for concurrent access
            if self._db_type == "sqlite":
                self._configure_sqlite_concurrent_access()
            
            # Test the connection
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            
            logger.info(f"Database client initialized successfully with {self._db_type}")
            
            # Run migrations first, then auto-create/update tables
            self._run_migrations()
            self._auto_create_tables()
            self._run_audit_retention()

        except Exception as e:
            logger.error(f"Failed to initialize database client: {e}")
            self._engine = None
            self._session_factory = None
    
    def get_session(self) -> Optional[Session]:
        """Get a new database session."""
        if not self._session_factory:
            logger.error("Database session factory not initialized")
            return None
        return self._session_factory()
    
    def _run_migrations(self):
        """Run database migrations on initialization."""
        try:
            from .migration_system import MigrationSystem
            migration_system = MigrationSystem(self)
            success = migration_system.run_migrations()
            if success:
                logger.info("Database migrations completed successfully")
            else:
                logger.warning("Some database migrations may have failed")
        except Exception as e:
            logger.warning(f"Failed to run database migrations: {e}")

    def _run_audit_retention(self):
        """Run audit log retention (delete entries older than AUDIT_RETENTION_DAYS)."""
        try:
            from .audit_service import run_retention
            result = run_retention()
            if result.get("deleted_count", 0) > 0:
                logger.info("Audit retention: deleted %s row(s)", result["deleted_count"])
        except Exception as e:
            logger.debug("Audit retention on startup: %s", e)

    def _auto_create_tables(self):
        """Automatically create/update tables on initialization."""
        if not self._engine:
            logger.warning("Database engine not initialized, skipping auto table creation")
            return False
        
        try:
            # Check if we can auto-create tables (enabled by default)
            auto_create = os.getenv("DB_AUTO_CREATE_TABLES", "true").lower() == "true"
            
            if not auto_create:
                logger.info("Auto table creation disabled via DB_AUTO_CREATE_TABLES=false")
                return True
            
            # Create all tables defined in models
            Base.metadata.create_all(self._engine)
            logger.info("Database tables auto-created/updated successfully")
            
            # Check if we need to populate initial data
            self._populate_initial_data_if_needed()
            
            return True
            
        except SQLAlchemyError as e:
            logger.warning(f"Failed to auto-create database tables: {e}")
            logger.info("You may need to run the setup script manually or check your database configuration")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error during auto table creation: {e}")
            return False
    
    def _configure_sqlite_concurrent_access(self):
        """Configure SQLite for concurrent access using WAL mode."""
        try:
            @event.listens_for(self._engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                """Set SQLite pragmas for concurrent access and performance."""
                cursor = dbapi_connection.cursor()
                
                # Enable WAL mode for concurrent reads/writes
                cursor.execute("PRAGMA journal_mode=WAL")
                
                # Set synchronous mode to NORMAL for better performance
                cursor.execute("PRAGMA synchronous=NORMAL")
                
                # Enable foreign keys
                cursor.execute("PRAGMA foreign_keys=ON")
                
                # Set busy timeout to 30 seconds
                cursor.execute("PRAGMA busy_timeout=30000")
                
                # Optimize cache size (negative value = KB, positive = pages)
                cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
                
                cursor.close()
            
            logger.info("SQLite configured for concurrent access with WAL mode")
            
        except Exception as e:
            logger.warning(f"Failed to configure SQLite concurrent access: {e}")
    
    def _populate_initial_data_if_needed(self):
        """Populate initial agent configurations if database is empty."""
        if not self._session_factory:
            return
        
        # Allow skipping seed data (e.g. during standalone builds)
        if os.getenv("DB_SKIP_SEED", "").lower() in ("true", "1", "yes"):
            logger.info("Skipping initial data population (DB_SKIP_SEED=true)")
            return
        
        session = self._session_factory()
        try:
            from .models import AgentConfig
            
            # Check if we have any agent configurations
            agent_count = session.query(AgentConfig).count()
            
            if agent_count == 0:
                logger.info("Database is empty, checking for initial data population")
                
                # Try to populate with default agent configurations
                self._create_default_agent_configs()
            else:
                logger.debug(f"Database already has {agent_count} agent configurations")
                
        except Exception as e:
            logger.warning(f"Failed to check/populate initial data: {e}")
        finally:
            session.close()
    
    def _create_default_agent_configs(self):
        """Create default agent configurations programmatically."""
        if not self._session_factory:
            logger.warning("No session factory available for creating default configs")
            return
        
        from .models import AgentConfig, Project
        
        session = self._session_factory()
        try:
            default_project = session.query(Project).filter(Project.name == 'Default Project').first()
            if not default_project:
                default_project = Project(
                    name='Default Project',
                    description='Default project for seeded agents'
                )
                session.add(default_project)
                session.flush()
            default_project_id = default_project.id
            
            # Define default agent configurations (MATE chess_mate_root tree)
            default_agents = [
                {
                    'name': 'chess_mate_root',
                    'type': 'root',
                    'model_name': 'openrouter/deepseek/deepseek-chat-v3.1',
                    'description': 'MATE root: Routes chess-related queries to opening book, engine analyst, or historian sub-agents.',
                    'instruction': '''You are the MATE (Multi-Agent Tree Engine) chess root agent. Route queries to the appropriate sub-agent:

1. chess_opening_book: Opening theory, opening names, opening lines, book moves, repertoire
2. chess_engine_analyst: Analysis, evaluation, best move, engine lines, tactical/strategic analysis
3. chess_historian: Historical games, famous players, tournaments, chess history, game search

For simple single-topic queries, delegate to the right agent and let them respond. For complex queries needing multiple agents, orchestrate steps and synthesize results.''',
                    'parent_agents': None,
                    'allowed_for_roles': '["admin", "user", "guest"]',
                    'disabled': False
                },
                {
                    'name': 'chess_opening_book',
                    'type': 'llm',
                    'model_name': 'openrouter/deepseek/deepseek-chat-v3.1',
                    'description': 'Knowledge/Context: Opening theory, book lines, and repertoire.',
                    'instruction': '''You are the Chess Opening Book agent. Answer questions about opening theory, named openings, main lines, and repertoire. Use your knowledge and any provided tools. For simple opening questions respond directly; for multi-topic requests return control to the root.''',
                    'parent_agents': '["chess_mate_root"]',
                    'allowed_for_roles': '["admin", "user", "guest"]',
                    'disabled': False
                },
                {
                    'name': 'chess_engine_analyst',
                    'type': 'llm',
                    'model_name': 'openrouter/deepseek/deepseek-chat-v3.1',
                    'description': 'Hybrid/Computation: Position analysis and engine-style evaluation.',
                    'instruction': '''You are the Chess Engine Analyst agent. Handle position analysis, best moves, evaluations, and tactical/strategic discussion. For simple analysis requests respond directly; for multi-topic requests return control to the root.''',
                    'parent_agents': '["chess_mate_root"]',
                    'allowed_for_roles': '["admin", "user", "guest"]',
                    'disabled': False
                },
                {
                    'name': 'chess_historian',
                    'type': 'llm',
                    'model_name': 'openrouter/deepseek/deepseek-chat-v3.1',
                    'description': 'Search Tools: Historical games, famous players, and chess history.',
                    'instruction': '''You are the Chess Historian agent. Answer questions about historical games, famous players, tournaments, and chess history. Use search tools when needed. For simple history questions respond directly; for multi-topic requests return control to the root.''',
                    'parent_agents': '["chess_mate_root"]',
                    'allowed_for_roles': '["admin", "user", "guest"]',
                    'tool_config': '{"google_search": true}',
                    'disabled': False
                }
            ]
            
            # Create agent configuration objects
            created_count = 0
            for agent_data in default_agents:
                payload = dict(agent_data)
                payload.setdefault('project_id', default_project_id)
                agent = AgentConfig(**payload)
                session.add(agent)
                created_count += 1
            
            session.commit()
            logger.info(f"Successfully created {created_count} default agent configurations")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to create default agent configurations: {e}")
            raise
        finally:
            session.close()
    
    def _clean_sql_for_database_type(self, sql_content: str) -> str:
        """Clean SQL content to work with different database types."""
        # Remove PostgreSQL-specific schema prefixes for SQLite/MySQL
        if self._db_type in ['sqlite', 'mysql']:
            sql_content = sql_content.replace('public.agents_config', 'agents_config')
            sql_content = sql_content.replace('public.token_usage_logs', 'token_usage_logs')
            sql_content = sql_content.replace('public.users', 'users')
        
        return sql_content
    
    def create_tables(self):
        """Create all tables defined in models."""
        if not self._engine:
            logger.error("Database engine not initialized")
            return False
        
        try:
            Base.metadata.create_all(self._engine)
            logger.info("Database tables created successfully")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Failed to create database tables: {e}")
            return False
    
    def is_connected(self) -> bool:
        """Check if the client is properly connected."""
        if not self._engine:
            return False
        
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
    
    def get_connection_info(self) -> dict:
        """Get database connection information for display."""
        info = {
            "type": self._db_type.upper(),
            "hostname": None,
            "filename": None,
            "database": None,
            "port": None
        }
        
        if self._db_type == "sqlite":
            database_path = os.getenv("DB_PATH", "my_agent_data.db")
            if not os.path.isabs(database_path):
                current_dir = Path(__file__).parent
                project_root = current_dir.parent.parent
                database_path = str(project_root / database_path)
            info["filename"] = os.path.basename(database_path)
        elif self._db_type == "postgresql":
            info["hostname"] = os.getenv("DB_HOST", "localhost")
            info["database"] = os.getenv("DB_NAME", "")
            info["port"] = os.getenv("DB_PORT", "5432")
        elif self._db_type == "mysql":
            info["hostname"] = os.getenv("DB_HOST", "localhost")
            info["database"] = os.getenv("DB_NAME", "")
            info["port"] = os.getenv("DB_PORT", "3306")
        
        return info

    def close(self):
        """Close the database connection."""
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None


# Global instance
_database_client = None

def get_database_client() -> DatabaseClient:
    """Get the global database client instance."""
    global _database_client
    if _database_client is None:
        _database_client = DatabaseClient()
    return _database_client
