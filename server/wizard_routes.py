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
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response
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
TRIAL_TTL_DAYS_TIER3 = int(os.getenv("WIZARD_TIER3_TRIAL_TTL_DAYS", "2"))
TRIAL_MAX_PAGES = int(os.getenv("WIZARD_TRIAL_MAX_PAGES", "5"))

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


def _partner_origin_ok(request: Request, partner_data: Optional[dict]) -> bool:
    """Enforce a partner's origin allowlist.

    Allows: no partner / no restriction; a matching parent-page origin (the top-level /embed
    Referer); or same-host calls — the wizard's own in-iframe API calls carry the MATE /embed
    URL as Referer, so legitimate API traffic is allowed while cross-site direct calls are not.
    """
    if not partner_data:
        return True
    allowed = partner_data.get("allowed_origins") or []
    if not allowed:
        return True
    from shared.utils.wizard import partners as _p
    ref = request.headers.get("referer") or request.headers.get("origin") or ""
    req_origin = _p._origin_of(ref)
    this_host = (request.headers.get("host") or "").lower()
    if req_origin and this_host and req_origin.split("://", 1)[-1] == this_host:
        return True
    return _p.origin_allowed(partner_data, ref)


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
                       lang: Optional[str] = Query(None), contact: Optional[str] = Query(None),
                       currency: Optional[str] = Query(None), partner: Optional[str] = Query(None),
                       fresh: bool = Query(False)):
    """Serve the wizard page (loaded inside an iframe on the customer's site).

    ``partner`` selects a site config (own pricing, allowed origins, contact). ``lang``,
    ``currency`` and ``contact`` can override the partner defaults.
    """
    from shared.utils.wizard import partners as partners_svc
    partner_data = partners_svc.get_partner(partner) if partner else None

    # Enforce the partner's origin allowlist (where the iframe may be embedded).
    if not _partner_origin_ok(request, partner_data):
        return HTMLResponse("<h3>This agent wizard is not enabled for this site.</h3>", status_code=403)

    partner_key = partner_data["partner_key"] if partner_data else ""
    lang_eff = lang or (partner_data or {}).get("default_lang")
    contact_eff = (contact or "").strip() or (partner_data or {}).get("contact_email") or pricing.get_contact_email()
    return templates.TemplateResponse(request, "wizard/wizard.html", {
        "request": request,
        "preselect_tier": tier or "",
        "lang": pricing.normalize_lang(lang_eff),
        "currency": pricing.normalize_currency(currency, partner=partner_key),
        "contact_email": contact_eff,
        "partner": partner_key,
        "fresh": fresh,
    })


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def wizard_demo():
    """Serve the developer demo/test harness for the wizard embed."""
    demo_path = project_root / "static" / "wizard-demo.html"
    return FileResponse(str(demo_path), media_type="text/html")


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
async def list_tiers(lang: Optional[str] = Query(None), currency: Optional[str] = Query(None),
                     partner: Optional[str] = Query(None)):
    """Return the tier catalogue (localized) with prices for the requested currency + partner."""
    from shared.utils.wizard import partners as partners_svc
    partner_data = partners_svc.get_partner(partner) if partner else None
    partner_key = partner_data["partner_key"] if partner_data else None
    currency = pricing.normalize_currency(currency, partner=partner_key)
    tiers = []
    for t in pricing.get_tiers(lang=pricing.normalize_lang(lang), currency=currency, partner=partner_key):
        provisionable = bool(t["provisionable"]) and _tier_has_template(t["id"])
        tiers.append({**t, "provisionable": provisionable})
    contact = (partner_data or {}).get("contact_email") or pricing.get_contact_email()
    return {"tiers": tiers, "contact_email": contact, "currency": currency,
            "capabilities": pricing.get_capabilities(pricing.normalize_lang(lang))}


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
    partner_key = (body.get("partner") or "").strip() or None

    if partner_key:
        from shared.utils.wizard import partners as partners_svc
        partner_data = partners_svc.get_partner(partner_key)
        if not _partner_origin_ok(request, partner_data):
            raise HTTPException(status_code=403, detail="Not enabled for this site.")

    token = secrets.token_urlsafe(32)
    db = get_database_client()
    session = db.get_session()
    try:
        ws = WizardSession(
            session_token=token,
            tier=tier if tier in {"tier1", "tier2", "tier3", "tier4"} else None,
            status="started",
            partner_key=partner_key,
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

    reprovision = bool(body.get("reprovision"))
    old_pid = None
    db = get_database_client()
    session = db.get_session()
    try:
        ws = session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
        if not ws:
            raise HTTPException(status_code=404, detail="Session not found")
        if ws.widget_api_key and not reprovision:
            # Already provisioned — return existing trial (idempotent).
            return {
                "widget_api_key": ws.widget_api_key,
                "chat_url": f"/widget/chat?key={ws.widget_api_key}",
                "project_id": ws.trial_project_id,
                "root_agent_name": ws.root_agent_name,
            }
        if ws.widget_api_key and reprovision:
            # Config changed after provisioning — drop the old trial and rebuild.
            old_pid = ws.trial_project_id
            ws.widget_api_key = None
            ws.trial_project_id = None
            ws.root_agent_name = None
        tier = ws.tier
        step_data = ws.get_step_data()
        partner_key = ws.partner_key
        ws.status = "provisioning"
        session.commit()
    finally:
        session.close()

    if partner_key:
        from shared.utils.wizard import partners as partners_svc
        if not _partner_origin_ok(request, partners_svc.get_partner(partner_key)):
            raise HTTPException(status_code=403, detail="Not enabled for this site.")

    if old_pid:
        try:
            from shared.utils.wizard.cleanup import release_trial
            release_trial(old_pid)
        except Exception as exc:
            logger.warning("Could not release old trial %s on reprovision: %s", old_pid, exc)

    if tier not in TIER_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Tier '{tier}' is not provisionable")

    dashboard_server = getattr(request.app.state, "dashboard_server", None)
    if dashboard_server is None or dashboard_server.db_client is None:
        raise HTTPException(status_code=503, detail="Provisioning service unavailable")

    # Crawl the site and analyze it BEFORE provisioning, so the agent's description +
    # instruction (e.g. bookable services / appointment reasons) are tailored to the business.
    site_url = (step_data.get("site_url") or "").strip()
    pages = None
    analysis = None
    if site_url:
        try:
            from shared.utils.wizard.site_crawler import crawl_site
            pages = await crawl_site(site_url, session_token=token, max_pages=TRIAL_MAX_PAGES)
        except Exception as exc:
            logger.warning("Site crawl failed for %s: %s", site_url, exc)
            pages = None
        if pages:
            try:
                from shared.utils.wizard.site_analyzer import analyze_site
                analysis = analyze_site(pages, site_url=site_url)
            except Exception as exc:
                logger.warning("Site analysis failed for %s: %s", site_url, exc)
                analysis = None

    svc = WizardProvisioningService(dashboard_server)
    ttl = TRIAL_TTL_DAYS_TIER3 if tier == "tier3" else TRIAL_TTL_DAYS
    result = svc.provision_trial(tier, step_data, token, ttl_days=ttl,
                                 origin=request.headers.get("referer"),
                                 pages=pages, analysis=analysis)
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

    result["pages_indexed"] = len(pages) if pages else 0
    if analysis:
        result["services_found"] = len(analysis.get("services") or [])
    return result


@router.post("/api/session/abandon")
async def wizard_session_abandon(request: Request):
    """Release an unused trial when the user leaves the wizard without submitting a lead.

    Called via navigator.sendBeacon on pagehide. Only acts when:
    - The session has a provisioned trial project, AND
    - No lead exists for that project (a lead means the trial has business value — keep it).

    Always returns 204; sendBeacon ignores the response body.
    """
    try:
        body = await request.body()
        import json as _json
        data = _json.loads(body) if body else {}
    except Exception:
        return Response(status_code=204)

    token = (data.get("token") or "").strip()
    if not token:
        return Response(status_code=204)

    db = get_database_client()
    session = db.get_session()
    try:
        ws = session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
        if not ws or not ws.trial_project_id:
            return Response(status_code=204)
        # Keep the trial if a lead was captured — admin may want to review it.
        lead_exists = session.query(WizardLead).filter(
            WizardLead.trial_project_id == ws.trial_project_id
        ).first()
        if lead_exists:
            return Response(status_code=204)
        project_id = ws.trial_project_id
    finally:
        session.close()

    try:
        from shared.utils.wizard.cleanup import release_trial
        release_trial(project_id)
        logger.info("Abandoned wizard trial project %s released on pagehide", project_id)
    except Exception as exc:
        logger.warning("Could not release abandoned trial %s: %s", project_id, exc)

    return Response(status_code=204)


@router.post("/api/lead")
async def submit_lead(request: Request):
    """Capture the prospect's contact details as a lead and snapshot the price estimate."""
    body = await request.json()
    token = body.get("session_token")
    email = (body.get("email") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    tier = body.get("tier")
    currency = body.get("currency")
    requirements = body.get("requirements")

    db = get_database_client()
    session = db.get_session()
    try:
        ws = session.query(WizardSession).filter(
            WizardSession.session_token == token
        ).first()
        if ws and not tier:
            tier = ws.tier
        partner_key = (ws.partner_key if ws else None) or (body.get("partner") or "").strip() or None

        lead = WizardLead(
            wizard_session_id=ws.id if ws else None,
            tier=tier,
            name=(body.get("name") or "").strip() or None,
            email=email,
            company=(body.get("company") or "").strip() or None,
            phone=(body.get("phone") or "").strip() or None,
            message=(body.get("message") or "").strip() or None,
            estimated_price=pricing.get_estimated_price(tier, currency, partner_key) if tier else None,
            partner_key=partner_key,
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
