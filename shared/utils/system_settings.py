"""
Server-wide settings changed in the dashboard (the Settings page).

A stored value wins over the matching environment variable, and clearing it
falls back to that variable. Reads hit the database each time: the agent
runtime is a separate process, so a cached value would not see a change made
in the dashboard.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from .database_client import get_database_client
from .models import SystemSetting

logger = logging.getLogger(__name__)

IMAGE_MODEL = "image_model"


def get_setting(key: str) -> Optional[str]:
    """The stored value, or None when unset or the database is unavailable. Never raises."""
    db = get_database_client()
    session = db.get_session() if db else None
    if not session:
        return None
    try:
        row = session.get(SystemSetting, key)
        return row.value if row and row.value else None
    except Exception as e:
        logger.warning("Could not read setting %s: %s", key, e)
        return None
    finally:
        session.close()


def set_setting(key: str, value: Optional[str], actor: str) -> bool:
    """Store a value, or delete the row when value is empty. Returns success."""
    db = get_database_client()
    session = db.get_session() if db else None
    if not session:
        return False
    try:
        row = session.get(SystemSetting, key)
        if not value:
            if row:
                session.delete(row)
        elif row:
            row.value, row.updated_by = value, actor
            row.updated_at = datetime.now(timezone.utc)
        else:
            session.add(SystemSetting(setting_key=key, value=value, updated_by=actor))
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logger.error("Could not store setting %s: %s", key, e)
        return False
    finally:
        session.close()
