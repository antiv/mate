"""
Environment settings that more than one module reads, each with one default.

These variables used to be read in several places with different fallbacks:
the migration system fell back to PostgreSQL and `mate_agent.db` while the
application used SQLite and `my_agent_data.db`, the session store used port
5432 for MySQL, and the Supabase tools wrote to `public-bucket` while the
artifact services wrote to `artifacts`. Read them here instead of calling
os.getenv with a default of your own.
"""

import os
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DB_TYPES = ("sqlite", "postgresql", "mysql")
_DEFAULT_PORTS = {"postgresql": "5432", "mysql": "3306"}


def db_type() -> str:
    """`sqlite`, `postgresql` or `mysql`."""
    return os.getenv("DB_TYPE", "sqlite").lower()


def db_path() -> str:
    """The SQLite file, absolute; a relative DB_PATH is taken from the project root."""
    path = Path(os.getenv("DB_PATH", "my_agent_data.db"))
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


def db_host() -> str:
    return os.getenv("DB_HOST", "localhost")


def db_port(kind: Optional[str] = None) -> str:
    """DB_PORT, else the default port of the database type."""
    return os.getenv("DB_PORT", _DEFAULT_PORTS.get(kind or db_type(), "5432"))


def db_name() -> str:
    return os.getenv("DB_NAME", "mate_agent")


def db_user() -> Optional[str]:
    return os.getenv("DB_USER") or None


def db_password() -> Optional[str]:
    return os.getenv("DB_PASSWORD") or None


def database_url() -> str:
    """The synchronous SQLAlchemy URL for the application database.

    Names psycopg2 explicitly: from SQLAlchemy 2.1 a bare postgresql:// selects
    psycopg (v3), which is not installed. Raises ValueError when the type is
    unknown or a server database lacks its user or password.
    """
    kind = db_type()
    if kind == "sqlite":
        return f"sqlite:///{db_path()}"
    if kind not in DB_TYPES:
        raise ValueError(f"Unsupported database type: {kind}")
    user, password = db_user(), db_password()
    label = "PostgreSQL" if kind == "postgresql" else "MySQL"
    if not (user and password):
        raise ValueError(f"{label} requires DB_USER and DB_PASSWORD")
    driver = "postgresql+psycopg2" if kind == "postgresql" else "mysql+pymysql"
    return f"{driver}://{user}:{password}@{db_host()}:{db_port(kind)}/{db_name()}"


def database_info() -> dict:
    """What the dashboard shows about the database: no credentials."""
    kind = db_type()
    info = {"type": kind.upper(), "hostname": None, "filename": None,
            "database": None, "port": None}
    if kind == "sqlite":
        info["filename"] = os.path.basename(db_path())
    elif kind in DB_TYPES:
        info.update(hostname=db_host(), database=db_name(), port=db_port(kind))
    return info


def artifact_service() -> str:
    """`local_folder`, `supabase`, `s3`, or `none` (in memory, lost on restart)."""
    return os.getenv("ARTIFACT_SERVICE", "none").lower()


def supabase_bucket() -> str:
    """The bucket for both the Supabase artifact service and the Supabase tools."""
    return os.getenv("SUPABASE_BUCKET", "artifacts")
