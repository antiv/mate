"""
Audit logging service for EU AI Act compliance.
Append-only log: no UPDATE/DELETE from application code.
Retention: configurable auto-delete after N days (AUDIT_RETENTION_DAYS).
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from .database_client import get_database_client
from .models import AuditLog

logger = logging.getLogger(__name__)

# Action constants for consistency
ACTION_AGENT_CREATE = "agent.create"
ACTION_AGENT_UPDATE = "agent.update"
ACTION_AGENT_DELETE = "agent.delete"
ACTION_USER_CREATE = "user.create"
ACTION_USER_UPDATE = "user.update"
ACTION_USER_DELETE = "user.delete"
ACTION_PROJECT_CREATE = "project.create"
ACTION_PROJECT_UPDATE = "project.update"
ACTION_PROJECT_DELETE = "project.delete"
ACTION_CONFIG_CHANGE = "config.change"
ACTION_RBAC_DENIAL = "rbac.denial"
ACTION_LOGIN = "auth.login"
ACTION_LOGOUT = "auth.logout"
ACTION_KEY_CREATE = "widget_key.create"
ACTION_KEY_UPDATE = "widget_key.update"
ACTION_KEY_DELETE = "widget_key.delete"
ACTION_RATE_LIMIT_CREATE = "rate_limit.create"
ACTION_RATE_LIMIT_UPDATE = "rate_limit.update"
ACTION_RATE_LIMIT_DELETE = "rate_limit.delete"
ACTION_MIGRATION_RUN = "migration.run"
ACTION_MIGRATION_ROLLBACK = "migration.rollback"
ACTION_SERVER_START = "server.start"
ACTION_SERVER_STOP = "server.stop"
ACTION_SERVER_RESTART = "server.restart"
ACTION_AGENT_ACCESS = "agent.access"
ACTION_AGENT_ROLLBACK = "agent.rollback"
ACTION_TEMPLATE_IMPORT = "template.import"
ACTION_FILE_STORE_CREATE = "file_store.create"
ACTION_FILE_STORE_DELETE = "file_store.delete"
ACTION_MEMORY_BLOCK_CREATE = "memory_block.create"
ACTION_MEMORY_BLOCK_UPDATE = "memory_block.update"
ACTION_MEMORY_BLOCK_DELETE = "memory_block.delete"

RESOURCE_AGENT = "agent"
RESOURCE_USER = "user"
RESOURCE_PROJECT = "project"
RESOURCE_WIDGET_KEY = "widget_key"
RESOURCE_RATE_LIMIT = "rate_limit"
RESOURCE_MIGRATION = "migration"
RESOURCE_SERVER = "server"
RESOURCE_FILE_STORE = "file_store"
RESOURCE_MEMORY_BLOCK = "memory_block"
RESOURCE_AUTH = "auth"


def _client_ip(request: Any) -> Optional[str]:
    """Extract client IP from FastAPI Request (supports X-Forwarded-For)."""
    if request is None:
        return None
    forwarded = getattr(request, "headers", None) and request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    if client:
        return getattr(client, "host", None)
    return None


def log(
    actor: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request: Any = None,
) -> None:
    """
    Append a single audit log entry. Never raises; logs and swallows errors.
    """
    ip = _client_ip(request) if request is not None else None
    db = get_database_client()
    if not db:
        logger.warning("Audit log: no database client, skipping log")
        return
    session = db.get_session()
    if not session:
        logger.warning("Audit log: no session, skipping log")
        return
    try:
        entry = AuditLog(
            actor=actor or "system",
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip_address=ip,
        )
        entry.set_details(details)
        session.add(entry)
        session.commit()
    except Exception as e:
        logger.warning("Audit log write failed: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()


def run_retention() -> Dict[str, Any]:
    """
    Delete audit log entries older than AUDIT_RETENTION_DAYS.
    Returns dict with deleted_count and cutoff date.
    """
    days = int(os.getenv("AUDIT_RETENTION_DAYS", "0"))
    if days <= 0:
        return {"deleted_count": 0, "retention_days": 0, "message": "Retention disabled (AUDIT_RETENTION_DAYS <= 0)"}
    db = get_database_client()
    if not db:
        return {"error": "No database client", "deleted_count": 0}
    session = db.get_session()
    if not session:
        return {"error": "No session", "deleted_count": 0}
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = session.query(AuditLog).filter(AuditLog.timestamp < cutoff).delete()
        session.commit()
        logger.info("Audit retention: deleted %d rows older than %s", deleted, cutoff.isoformat())
        return {"deleted_count": deleted, "retention_days": days, "cutoff": cutoff.isoformat()}
    except Exception as e:
        logger.warning("Audit retention failed: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return {"error": str(e), "deleted_count": 0}
    finally:
        session.close()


def get_retention_days() -> int:
    """Return configured retention days (0 = keep forever)."""
    return max(0, int(os.getenv("AUDIT_RETENTION_DAYS", "0")))
