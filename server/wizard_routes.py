"""Public Agent Builder Wizard routes.

Serves an embeddable iframe wizard that lets a prospect (no login) pick an agent tier,
configure it, provision a live trial agent, test it in an embedded widget chat, and finally
leave their contact details as a lead. There is no payment — the wizard ends with a price
estimate and a "contact us" instruction.

All write endpoints are authenticated by an opaque per-session token created at
``/wizard/api/session/start``. The expensive endpoints (start, provision) are throttled
per-IP in process.
"""

import logging
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from shared.utils.database_client import get_database_client
from shared.utils.models import WizardSession, WizardLead
from shared.utils.template_service import TemplateService
from shared.utils.wizard import pricing
from shared.utils.wizard.provisioning_service import WizardProvisioningService, TIER_TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wizard", tags=["Wizard"])

project_root = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(project_root / "templates"))
_template_service = TemplateService(project_root)


def _tier_has_template(tier_id: str) -> bool:
    """A tier can provision a live trial only if its agent template file exists."""
    template_id = TIER_TEMPLATES.get(tier_id)
    return bool(template_id) and _template_service.get_template(template_id) is not None

import os

TRIAL_TTL_DAYS = int(os.getenv("WIZARD_TRIAL_TTL_DAYS", "7"))

# --- Lightweight in-process per-IP throttle for the costly endpoints -----------
_RATE_BUCKET: dict = defaultdict(list)
_RATE_LIMITS = {
    "start": (10, 3600),       # 10 new sessions / hour / IP
    "provision": (5, 3600),    # 5 trial provisions / hour / IP
}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(action: str, ip: str) -> bool:
    limit, window = _RATE_LIMITS[action]
    now = time.time()
    key = f"{action}:{ip}"
    hits = [t for t in _RATE_BUCKET[key] if now - t < window]
    if len(hits) >= limit:
        _RATE_BUCKET[key] = hits
        return True
    hits.append(now)
    _RATE_BUCKET[key] = hits
    return False


def _verify_captcha(token: Optional[str]) -> bool:
    """No-op captcha hook. When WIZARD_CAPTCHA_PROVIDER is unset, always passes.

    Pluggable later (hCaptcha / Turnstile) without touching call sites.
    """
    provider = os.getenv("WIZARD_CAPTCHA_PROVIDER")
    if not provider:
        return True
    return bool(token)


def _get_session(token: str) -> Optional[WizardSession]:
    if not token:
        return None
    db = get_database_client()
    session = db.get_session()
    try:
        return session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Pages / static assets
# ---------------------------------------------------------------------------

@router.get("/embed", response_class=HTMLResponse, include_in_schema=False)
async def wizard_embed(request: Request, tier: Optional[str] = Query(None),
                       lang: Optional[str] = Query(None), contact: Optional[str] = Query(None)):
    """Serve the wizard page (loaded inside an iframe on the customer's site).

    ``lang`` (en|sr) and ``contact`` (override email) are passed by the embedding site.
    """
    return templates.TemplateResponse(request, "wizard/wizard.html", {
        "request": request,
        "preselect_tier": tier or "",
        "lang": pricing.normalize_lang(lang),
        "contact_email": (contact or "").strip() or pricing.get_contact_email(),
    })


@router.get("/mate-wizard.js", include_in_schema=False)
async def serve_wizard_loader():
    """Serve the embeddable loader script that injects the wizard iframe."""
    js_path = project_root / "static" / "js" / "wizard" / "mate-wizard.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Wizard loader not found")
    return FileResponse(js_path, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@router.get("/api/tiers")
async def list_tiers(lang: Optional[str] = Query(None)):
    """Return the tier catalogue (localized) with display prices for the wizard UI."""
    tiers = []
    for t in pricing.get_tiers(lang=pricing.normalize_lang(lang)):
        provisionable = bool(t["provisionable"]) and _tier_has_template(t["id"])
        tiers.append({**t, "provisionable": provisionable})
    return {"tiers": tiers, "contact_email": pricing.get_contact_email()}


@router.get("/api/session/{token}")
async def get_session_state(token: str):
    """Return a session's state so the wizard can resume after a page refresh."""
    ws = _get_session(token)
    if not ws:
        raise HTTPException(status_code=404, detail="Session not found")
    if ws.status == "expired":
        raise HTTPException(status_code=410, detail="Session expired")
    return {
        "tier": ws.tier,
        "status": ws.status,
        "step_data": ws.get_step_data(),
        "widget_api_key": ws.widget_api_key,
        "chat_url": f"/widget/chat?key={ws.widget_api_key}" if ws.widget_api_key else None,
        "root_agent_name": ws.root_agent_name,
    }


@router.post("/api/session/start")
async def start_session(request: Request):
    """Create a wizard session and return its opaque token."""
    ip = _client_ip(request)
    if _rate_limited("start", ip):
        raise HTTPException(status_code=429, detail="Too many sessions, please try again later.")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    tier = body.get("tier")

    token = secrets.token_urlsafe(32)
    db = get_database_client()
    session = db.get_session()
    try:
        ws = WizardSession(
            session_token=token,
            tier=tier if tier in {"tier1", "tier2", "tier3", "tier4"} else None,
            status="started",
            client_ip=ip,
            origin=request.headers.get("referer") or request.headers.get("origin"),
        )
        session.add(ws)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to start wizard session: %s", exc)
        raise HTTPException(status_code=500, detail="Could not start session")
    finally:
        session.close()

    return {"session_token": token}


@router.post("/api/session/step")
async def save_step(request: Request):
    """Merge step inputs into the session's accumulated step_data."""
    body = await request.json()
    token = body.get("session_token")
    tier = body.get("tier")
    data = body.get("data") or {}

    db = get_database_client()
    session = db.get_session()
    try:
        ws = session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Session not found")
        if tier in {"tier1", "tier2", "tier3", "tier4"}:
            ws.tier = tier
        merged = ws.get_step_data()
        merged.update(data)
        ws.set_step_data(merged)
        session.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to save wizard step: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save step")
    finally:
        session.close()


@router.post("/api/session/provision")
async def provision(request: Request):
    """Provision a live trial agent for the session's chosen tier."""
    ip = _client_ip(request)
    if _rate_limited("provision", ip):
        raise HTTPException(status_code=429, detail="Too many trials, please try again later.")

    body = await request.json()
    token = body.get("session_token")
    if not _verify_captcha(body.get("captcha_token")):
        raise HTTPException(status_code=400, detail="Captcha verification failed")

    db = get_database_client()
    session = db.get_session()
    try:
        ws = session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Session not found")
        if ws.widget_api_key:
            # Already provisioned — return existing trial (idempotent).
            return {
                "widget_api_key": ws.widget_api_key,
                "chat_url": f"/widget/chat?key={ws.widget_api_key}",
                "project_id": ws.trial_project_id,
                "root_agent_name": ws.root_agent_name,
            }
        tier = ws.tier
        step_data = ws.get_step_data()
        ws.status = "provisioning"
        session.commit()
    finally:
        session.close()

    if tier not in TIER_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Tier '{tier}' is not provisionable")

    dashboard_server = getattr(request.app.state, "dashboard_server", None)
    if dashboard_server is None or dashboard_server.db_client is None:
        raise HTTPException(status_code=503, detail="Provisioning service unavailable")

    svc = WizardProvisioningService(dashboard_server)
    result = svc.provision_trial(tier, step_data, token, ttl_days=TRIAL_TTL_DAYS,
                                 origin=request.headers.get("referer"))
    if result.get("error"):
        # Mark failed so the session reflects reality.
        db2 = get_database_client()
        s2 = db2.get_session()
        try:
            ws2 = s2.query(WizardSession).filter(WizardSession.session_token == token).first()
            if ws2:
                ws2.status = "failed"
                s2.commit()
        finally:
            s2.close()
        raise HTTPException(status_code=500, detail=result["error"])

    # Preload site content into memory blocks (so the agent answers without browsing live).
    site_url = (step_data.get("site_url") or "").strip()
    if site_url:
        try:
            from shared.utils.wizard.site_crawler import crawl_site
            pages = await crawl_site(site_url, session_token=token)
            indexed = svc.store_site_memory_blocks(result["project_id"], pages)
            result["pages_indexed"] = indexed
        except Exception as exc:
            logger.warning("Site crawl failed for %s: %s", site_url, exc)
            result["pages_indexed"] = 0

    return result


@router.post("/api/lead")
async def submit_lead(request: Request):
    """Capture the prospect's contact details as a lead and snapshot the price estimate."""
    body = await request.json()
    token = body.get("session_token")
    email = (body.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    tier = body.get("tier")
    requirements = body.get("requirements")

    db = get_database_client()
    session = db.get_session()
    try:
        ws = session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
        if ws and not tier:
            tier = ws.tier

        lead = WizardLead(
            wizard_session_id=ws.id if ws else None,
            tier=tier,
            name=(body.get("name") or "").strip() or None,
            email=email,
            company=(body.get("company") or "").strip() or None,
            phone=(body.get("phone") or "").strip() or None,
            message=(body.get("message") or "").strip() or None,
            estimated_price=pricing.get_estimated_price(tier) if tier else None,
            trial_project_id=ws.trial_project_id if ws else None,
            trial_widget_key=ws.widget_api_key if ws else None,
            status="new",
            client_ip=_client_ip(request),
        )
        if isinstance(requirements, dict) and requirements:
            lead.set_requirements(requirements)

        # Snapshot the trial agent(s) + memory blocks as JSON so the built client
        # survives trial cleanup and can be reviewed/recreated later.
        if ws and ws.trial_project_id:
            try:
                dashboard_server = getattr(request.app.state, "dashboard_server", None)
                if dashboard_server is not None:
                    import json as _json
                    export = dashboard_server._export_agent_configs(project_id=ws.trial_project_id)
                    if export and "error" not in export:
                        lead.agent_snapshot = _json.dumps(export)
            except Exception as exc:
                logger.warning("Could not snapshot trial agent for lead: %s", exc)

        session.add(lead)
        if ws:
            ws.status = "lead_submitted"
        session.commit()
        return {"success": True, "contact_email": pricing.get_contact_email()}
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to save wizard lead: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save your details")
    finally:
        session.close()
