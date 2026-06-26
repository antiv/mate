"""Wizard partners: sites that embed the wizard, each with its own pricing, allowed origins
and lead attribution. Managed from the dashboard; referenced by the iframe via ``?partner=<key>``.
"""

import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from shared.utils.database_client import get_database_client
from shared.utils.models import WizardPartner

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")


def valid_key(key: str) -> bool:
    return bool(key and _KEY_RE.match(key))


def get_partner(partner_key: str) -> Optional[dict]:
    """Return an active partner's dict by key, or None."""
    if not partner_key:
        return None
    db = get_database_client()
    session = db.get_session()
    try:
        p = session.query(WizardPartner).filter(
            WizardPartner.partner_key == partner_key, WizardPartner.is_active == True  # noqa: E712
        ).first()
        return p.to_dict() if p else None
    finally:
        session.close()


def list_partners() -> List[dict]:
    db = get_database_client()
    session = db.get_session()
    try:
        return [p.to_dict() for p in session.query(WizardPartner).order_by(WizardPartner.partner_key).all()]
    finally:
        session.close()


def upsert_partner(partner_key: str, name: str = None, allowed_origins=None,
                   contact_email: str = None, default_lang: str = None,
                   pricing: dict = None, is_active: bool = True) -> dict:
    """Create or update a partner. Returns the saved dict or {"error": ...}."""
    if not valid_key(partner_key):
        return {"error": "Invalid partner key (use letters, digits, '-' or '_')."}
    db = get_database_client()
    session = db.get_session()
    try:
        p = session.query(WizardPartner).filter(WizardPartner.partner_key == partner_key).first()
        if not p:
            p = WizardPartner(partner_key=partner_key)
            session.add(p)
        if name is not None:
            p.name = name.strip() or None
        if allowed_origins is not None:
            p.set_allowed_origins([o.strip() for o in allowed_origins if str(o).strip()])
        if contact_email is not None:
            p.contact_email = contact_email.strip() or None
        if default_lang is not None:
            p.default_lang = default_lang.strip() or None
        if pricing is not None:
            p.set_pricing(pricing)
        if is_active is not None:
            p.is_active = bool(is_active)
        session.commit()
        return p.to_dict()
    except Exception as exc:
        session.rollback()
        logger.exception("upsert_partner failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


def delete_partner(partner_key: str) -> dict:
    db = get_database_client()
    session = db.get_session()
    try:
        session.query(WizardPartner).filter(WizardPartner.partner_key == partner_key).delete()
        session.commit()
        return {"success": True}
    finally:
        session.close()


def _origin_of(url: str) -> str:
    try:
        u = urlparse(url)
        if u.scheme and u.netloc:
            return f"{u.scheme}://{u.netloc}".lower()
    except Exception:
        pass
    return ""


def origin_allowed(partner: dict, request_origin_or_referer: str) -> bool:
    """True if the partner has no origin restriction, or the request origin matches one."""
    allowed = (partner or {}).get("allowed_origins") or []
    if not allowed:
        return True
    req = _origin_of(request_origin_or_referer)
    if not req:
        return False
    allowed_norm = {_origin_of(o) or o.strip().lower().rstrip("/") for o in allowed}
    return req in allowed_norm
