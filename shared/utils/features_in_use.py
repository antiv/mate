"""
Whether this installation uses the agent builder wizard and the shop's stored orders.

The Control Room sidebar shows their pages under Platform only when they are in use
(documents/design/dashboard-2.1.md, "Navigation"); the pages stay reachable by URL
either way.

- wizard: a wizard partner, lead or session exists.
- shop: a shop order is stored, or an agent's shop tool config sets a partner_key
  (shared/utils/tools/shop_tools.py), so its orders would be stored.

The answer is kept for CACHE_SECONDS, because every dashboard page asks.
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60

_lock = threading.Lock()
_cached: Optional[Dict[str, bool]] = None
_cached_at = 0.0


def _sets_partner_key(tool_config: Any) -> bool:
    try:
        config = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
    except (json.JSONDecodeError, TypeError):
        return False
    shop = config.get("shop") if isinstance(config, dict) else None
    return isinstance(shop, dict) and bool(str(shop.get("partner_key") or "").strip())


def _query() -> Dict[str, bool]:
    from .database_client import get_database_client
    from .models import AgentConfig, ShopOrder, WizardLead, WizardPartner, WizardSession
    session = get_database_client().get_session()
    try:
        wizard = any(session.query(model.id).first() is not None
                     for model in (WizardPartner, WizardLead, WizardSession))
        shop = session.query(ShopOrder.id).first() is not None
        if not shop:
            rows = session.query(AgentConfig.tool_config).filter(
                AgentConfig.tool_config.like('%partner_key%')).all()
            shop = any(_sets_partner_key(row[0]) for row in rows)
        return {"wizard": wizard, "shop": shop}
    finally:
        session.close()


def in_use() -> Dict[str, bool]:
    """{"wizard": bool, "shop": bool}; both False when the database can't be read."""
    global _cached, _cached_at
    with _lock:
        if _cached is not None and time.monotonic() - _cached_at < CACHE_SECONDS:
            return _cached
    try:
        result = _query()
    except Exception as e:
        logger.debug("Feature use check failed: %s", e)
        result = {"wizard": False, "shop": False}
    with _lock:
        _cached, _cached_at = result, time.monotonic()
    return result


def clear_cache() -> None:
    global _cached
    with _lock:
        _cached = None
