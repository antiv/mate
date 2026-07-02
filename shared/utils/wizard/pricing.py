"""Pricing / tier configuration for the Agent Builder Wizard.

There is no billing in MATE — these values are display-only. The wizard shows the estimate and
a "contact us" instruction; the chosen estimate is snapshotted onto the lead.

Tier **labels/descriptions/features** are localized (en/sr) and live in this file. Tier **prices**
(per currency) and the **default currency** are stored in the database (`wizard_config` key
``pricing``) and edited from the dashboard (Wizard Pricing page) — no code change / restart needed.
The values below are only seed defaults used until the dashboard saves a config.

Language and currency are passed in by the embedding iframe (``?lang=`` / ``?currency=``).
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "sr")

# Seed prices (used until the dashboard saves a pricing config). Ready-to-display strings.
# These are only defaults — the live base price is configured in the DB (wizard_config 'pricing')
# and per-partner, editable from the dashboard with no code change.
_DEFAULT_PRICES = {
    "tier1": {"EUR": "€50", "RSD": "5.900 RSD"},
    "tier2": {"EUR": "€125", "RSD": "14.900 RSD"},
    "tier3": {"EUR": "€255", "RSD": "29.900 RSD"},
    "tier4": {"EUR": "€300", "RSD": "35.000 RSD"},
}
_SEED_DEFAULT_CURRENCY = os.getenv("WIZARD_CURRENCY", "EUR").strip().upper()

_DEFAULT_SHOW_FROM = {
    "tier1": False,
    "tier2": False,
    "tier3": False,
    "tier4": True,
}


def is_empty_or_zero(val: str) -> bool:
    if not val:
        return True
    v = val.strip()
    if not v:
        return True
    digits = [c for c in v if c.isdigit()]
    if not digits:
        return True
    return all(d == '0' for d in digits)


def format_with_from_prefix(val: str, lang: str, show_from: bool) -> str:
    cleaned_val = val.strip()
    lower_val = cleaned_val.lower()
    if lower_val.startswith("from "):
        cleaned_val = cleaned_val[5:].strip()
    elif lower_val.startswith("od "):
        cleaned_val = cleaned_val[3:].strip()
    if not show_from:
        return cleaned_val
    prefix = "od " if lang == "sr" else "from "
    return prefix + cleaned_val


# Localized tier copy (labels/descriptions/features). tier4 carries a currency-independent
# "Custom" price label per language.
WIZARD_TIERS = [
    {
        "id": "tier1",
        "provisionable": True,
        "priced": True,
        "i18n": {
            "en": {
                "label": "AI Support",
                "description": "Set up a chatbot in 1 minute by entering your link. It learns from your site and answers your customers' questions 24/7 with accurate data.",
                "features": [
                    "Up to 1,500 conversations / month (a conversation = a session up to 24h or 15 interactions)",
                    "Training: up to 15 website pages (crawling) or 5 PDF/Word documents",
                    "Brand-colored chat widget — no coding needed",
                    "Automatic daily knowledge-base refresh (crawling)",
                    "Answers 24/7 from your website content",
                ],
            },
            "sr": {
                "label": "AI podrška",
                "description": "Postavite chat bota za 1 minut unošenjem vašeg linka. Sam uči sa vašeg sajta i odgovara na pitanja kupaca 24/7 tačnim podacima.",
                "features": [
                    "Do 1.500 razgovora mesečno (razgovor = sesija do 24h ili 15 interakcija)",
                    "Trening: do 15 stranica sajta (crawling) ili 5 PDF/Word dokumenata",
                    "Chat widget u bojama brenda — bez programiranja",
                    "Automatsko dnevno ažuriranje baze znanja (crawling)",
                    "Odgovara 24/7 sa sadržaja vašeg sajta",
                ],
            },
        },
    },
    {
        "id": "tier2",
        "provisionable": True,
        "priced": True,
        "i18n": {
            "en": {
                "label": "Support + Scheduling",
                "description": "Turns site visitors into clients: the agent finds free slots in your calendar and books appointments 24/7 — directly in chat.",
                "features": [
                    "Everything in AI Support",
                    "Up to 3,000 conversations / month, up to 3 calendars",
                    "Books appointments via interactive [[APPOINTMENT]] cards in chat",
                    "Synced with Google Calendar",
                    ".ics file in chat + email/calendar invite confirmation",
                ],
            },
            "sr": {
                "label": "Podrška + zakazivanje",
                "description": "Pretvara posetioce sajta u klijente: agent pronalazi slobodne termine u kalendaru i zakazuje sastanke 24/7 — direktno u četu.",
                "features": [
                    "Sve iz AI podrške",
                    "Do 3.000 razgovora mesečno, do 3 kalendara",
                    "Zakazivanje kroz interaktivne [[APPOINTMENT]] kartice u četu",
                    "Sinhronizacija sa Google kalendarom",
                    ".ics fajl u četu + potvrda mejlom / kalendarska pozivnica",
                ],
            },
        },
    },
    {
        "id": "tier3",
        "provisionable": True,
        "priced": True,
        "i18n": {
            "en": {
                "label": "AI Sales Agent",
                "description": "Your best online seller: recommends products based on the customer's needs and fills the cart directly in chat, 24/7.",
                "features": [
                    "Everything in Support + Scheduling",
                    "Up to 5,000 conversations / month, up to 1,000 catalog items",
                    "[[CARD]] rich cards — product image, price and 'Add to cart'",
                    "Real-time cart and order creation in chat",
                    "Connects to your store (Shopify, WooCommerce, custom)",
                ],
            },
            "sr": {
                "label": "AI prodavac",
                "description": "Vaš najbolji online prodavac: preporučuje artikle na osnovu želja kupca i puni korpu direktno u četu, 24/7.",
                "features": [
                    "Sve iz Podrške + zakazivanja",
                    "Do 5.000 razgovora mesečno, do 1.000 artikala u katalogu",
                    "[[CARD]] interaktivne kartice — slika, cena i „Dodaj u korpu“",
                    "Korpa u realnom vremenu i kreiranje porudžbine u četu",
                    "Povezivanje sa prodavnicom (Shopify, WooCommerce, custom)",
                ],
            },
        },
    },
    {
        "id": "tier4",
        "provisionable": False,
        "priced": True,
        "i18n": {
            "en": {
                "label": "Enterprise",
                "description": "Fully custom team of agents for your business process. One-time setup (≈€500) + monthly retainer; consultation and implementation by our team.",
                "price": "from €300",
                "features": [
                    "Complex multi-agent trees (support, pricing, security, logistics)",
                    "Advanced guardrails & safety (PII masking, prompt-injection protection)",
                    "Custom internal MCP servers (ERP, CRM, SAP)",
                    "MATE Eval Framework (prompt regression testing before go-live)",
                    "BYOK (bring your own API key) or fully metered billing",
                ],
            },
            "sr": {
                "label": "Enterprise",
                "description": "Potpuno prilagođen tim agenata za vaš poslovni proces. Jednokratni setup (≈59.000 RSD) + mesečni retainer; konsultacija i implementacija od strane našeg tima.",
                "price": "od 35.000 RSD",
                "features": [
                    "Kompleksna stabla agenata (podrška, cene, bezbednost, logistika)",
                    "Napredni guardrails i bezbednost (PII maskiranje, zaštita od prompt injection-a)",
                    "Custom interni MCP serveri (ERP, CRM, SAP)",
                    "MATE Eval Framework (regresiono testiranje promptova pre puštanja u rad)",
                    "BYOK (sopstveni API ključ) ili metered billing",
                ],
            },
        },
    },
]

_TIERS_BY_ID = {t["id"]: t for t in WIZARD_TIERS}
PRICING_CONFIG_KEY = "pricing"


def _apply_pricing_cfg(prices: dict, show_from: dict, cfg: dict):
    """Merge a {default_currency, prices, show_from} config dict into the working price map and show_from map. Returns default_currency or None."""
    default_currency = None
    db_prices = cfg.get("prices")
    if isinstance(db_prices, dict):
        for tier_id, pmap in db_prices.items():
            if isinstance(pmap, dict):
                prices[tier_id] = {str(k).upper(): str(v) for k, v in pmap.items() if v}
    db_show_from = cfg.get("show_from")
    if isinstance(db_show_from, dict):
        for tier_id, val in db_show_from.items():
            show_from[tier_id] = bool(val)
    if cfg.get("default_currency"):
        default_currency = str(cfg["default_currency"]).strip().upper()
    return default_currency



def _load_config(partner: str = None) -> dict:
    """Effective pricing: seed defaults <- global (wizard_config 'pricing') <- partner override.

    Returns ``{default_currency, currencies: [...], prices: {tier: {currency: display}}, show_from: {tier: bool}}``.
    """
    prices = {k: dict(v) for k, v in _DEFAULT_PRICES.items()}
    show_from = dict(_DEFAULT_SHOW_FROM)
    default_currency = _SEED_DEFAULT_CURRENCY

    # Global config row
    try:
        from shared.utils.database_client import get_database_client
        from shared.utils.models import WizardConfig
        session = get_database_client().get_session()
        try:
            row = session.query(WizardConfig).filter(WizardConfig.config_key == PRICING_CONFIG_KEY).first()
            if row and row.config_value:
                dc = _apply_pricing_cfg(prices, show_from, json.loads(row.config_value))
                if dc:
                    default_currency = dc
        finally:
            session.close()
    except Exception as exc:
        logger.debug("Pricing config DB load failed, using defaults: %s", exc)

    # Partner override
    if partner:
        try:
            from shared.utils.wizard import partners as _partners
            p = _partners.get_partner(partner)
            if p and p.get("pricing"):
                dc = _apply_pricing_cfg(prices, show_from, p["pricing"])
                if dc:
                    default_currency = dc
        except Exception as exc:
            logger.debug("Partner pricing load failed for %s: %s", partner, exc)

    currencies = sorted({c for pmap in prices.values() for c in pmap})
    if default_currency not in currencies and currencies:
        default_currency = currencies[0]
    return {"default_currency": default_currency, "currencies": currencies, "prices": prices, "show_from": show_from}


def normalize_lang(lang: Optional[str]) -> str:
    """Map an incoming language code to a supported one (default English)."""
    if not lang:
        return DEFAULT_LANG
    code = lang.split("-")[0].lower()
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def normalize_currency(currency: Optional[str], cfg: Optional[dict] = None, partner: str = None) -> str:
    """Map an incoming currency code to a configured one (default = configured default currency)."""
    cfg = cfg or _load_config(partner)
    if not currency:
        return cfg["default_currency"]
    code = currency.strip().upper()
    return code if code in cfg["currencies"] else cfg["default_currency"]


def get_currencies(partner: str = None) -> list:
    """List of configured currency codes."""
    return _load_config(partner)["currencies"]


def _resolve_price(tier: dict, lang: str, currency: str, cfg: dict) -> str:
    """Display price for a tier in the given currency (fallback to default currency / first)."""
    show_from = cfg.get("show_from", {}).get(tier["id"], False)
    if tier.get("priced"):
        pmap = cfg["prices"].get(tier["id"], {})
        val = pmap.get(currency) or pmap.get(cfg["default_currency"]) or (next(iter(pmap.values())) if pmap else "")
    else:
        loc = tier["i18n"].get(lang) or tier["i18n"][DEFAULT_LANG]
        val = loc.get("price", "")
    if is_empty_or_zero(val):
        return "Custom pricing"
    return format_with_from_prefix(val, lang, show_from)


def get_tiers(lang: str = DEFAULT_LANG, currency: str = None, partner: str = None) -> list:
    """Return the tier catalogue localized to ``lang`` with prices in ``currency`` (partner-aware)."""
    cfg = _load_config(partner)
    lang = normalize_lang(lang)
    currency = normalize_currency(currency, cfg)
    out = []
    for t in WIZARD_TIERS:
        loc = t["i18n"].get(lang) or t["i18n"][DEFAULT_LANG]
        out.append({
            "id": t["id"],
            "provisionable": t["provisionable"],
            "currency": currency if t.get("priced") else None,
            "monthly_estimate": _resolve_price(t, lang, currency, cfg),
            "label": loc["label"],
            "description": loc["description"],
            "features": loc["features"],
        })
    return out


def get_tier(tier_id: str) -> Optional[dict]:
    """Return a single (raw) tier config by id, or None."""
    return _TIERS_BY_ID.get(tier_id)


def get_estimated_price(tier_id: str, currency: str = None, partner: str = None) -> Optional[str]:
    """Return the price snapshot for a tier in the given currency (partner-aware), or None."""
    tier = _TIERS_BY_ID.get(tier_id)
    if not tier:
        return None
    cfg = _load_config(partner)
    return _resolve_price(tier, DEFAULT_LANG, normalize_currency(currency, cfg), cfg)


def get_pricing_config_for_admin(partner: str = None) -> dict:
    """Effective config + tier labels for the dashboard editor (optionally for a partner)."""
    cfg = _load_config(partner)
    tiers = [
        {"id": t["id"], "label": t["i18n"]["en"]["label"], "priced": bool(t.get("priced"))}
        for t in WIZARD_TIERS
    ]
    return {
        "default_currency": cfg["default_currency"],
        "currencies": cfg["currencies"],
        "prices": cfg["prices"],
        "show_from": cfg["show_from"],
        "tiers": tiers,
    }


def _clean_pricing_payload(default_currency: str, prices: dict, show_from: dict = None) -> dict:
    clean_prices = {}
    for tier_id, pmap in (prices or {}).items():
        if tier_id not in _TIERS_BY_ID or not _TIERS_BY_ID[tier_id].get("priced"):
            continue
        if isinstance(pmap, dict):
            clean_prices[tier_id] = {str(k).strip().upper(): str(v).strip()
                                     for k, v in pmap.items() if str(k).strip() and str(v).strip()}
    clean_show_from = {}
    for tier_id, val in (show_from or {}).items():
        if tier_id in _TIERS_BY_ID:
            clean_show_from[tier_id] = bool(val)
    return {
        "default_currency": (default_currency or "").strip().upper() or _SEED_DEFAULT_CURRENCY,
        "prices": clean_prices,
        "show_from": clean_show_from,
    }


def save_pricing_config(db_client, default_currency: str, prices: dict, show_from: dict = None, partner: str = None) -> dict:
    """Persist the pricing config — global (wizard_config) or for a specific ``partner``."""
    payload = _clean_pricing_payload(default_currency, prices, show_from)

    if partner:
        from shared.utils.wizard import partners as _partners
        res = _partners.upsert_partner(partner, pricing=payload)
        if res.get("error"):
            raise ValueError(res["error"])
        return get_pricing_config_for_admin(partner)

    from shared.utils.models import WizardConfig
    session = db_client.get_session()
    try:
        row = session.query(WizardConfig).filter(WizardConfig.config_key == PRICING_CONFIG_KEY).first()
        if row:
            row.config_value = json.dumps(payload)
        else:
            session.add(WizardConfig(config_key=PRICING_CONFIG_KEY, config_value=json.dumps(payload)))
        session.commit()
    finally:
        session.close()
    return get_pricing_config_for_admin()


# MATE platform capabilities shown on the Tier 4 (custom) step — the prospect ticks what
# they're interested in and it's forwarded into the lead. Localized; edit to taste.
WIZARD_CAPABILITIES = {
    "en": [
        {"id": "support", "label": "Website support chatbot (answers from your site)"},
        {"id": "scheduling", "label": "Appointment scheduling (calendar)"},
        {"id": "sales", "label": "Online sales — cart & orders"},
        {"id": "knowledge", "label": "Answers from your documents / knowledge base"},
        {"id": "integrations", "label": "Connect your tools (MCP / API integrations)"},
        {"id": "automations", "label": "Multi-agent workflows & automations"},
        {"id": "multilingual", "label": "Multilingual support"},
        {"id": "widget", "label": "Embeddable chat widget for your site"},
        {"id": "leadcapture", "label": "Lead capture & qualification"},
        {"id": "custom", "label": "Something else / custom"},
    ],
    "sr": [
        {"id": "support", "label": "Chatbot za podršku na sajtu (odgovori sa vašeg sajta)"},
        {"id": "scheduling", "label": "Zakazivanje termina (kalendar)"},
        {"id": "sales", "label": "Online prodaja — korpa i porudžbine"},
        {"id": "knowledge", "label": "Odgovori iz vaših dokumenata / baza znanja"},
        {"id": "integrations", "label": "Povezivanje vaših alata (MCP / API integracije)"},
        {"id": "automations", "label": "Više-agentni tokovi i automatizacije"},
        {"id": "multilingual", "label": "Višejezička podrška"},
        {"id": "widget", "label": "Chat widget za ugradnju na sajt"},
        {"id": "leadcapture", "label": "Prikupljanje i kvalifikacija lidova"},
        {"id": "custom", "label": "Nešto drugo / po meri"},
    ],
}


def get_capabilities(lang: str = DEFAULT_LANG) -> list:
    """Localized list of platform capabilities for the Tier 4 step."""
    return WIZARD_CAPABILITIES.get(normalize_lang(lang), WIZARD_CAPABILITIES[DEFAULT_LANG])


def get_contact_email() -> str:
    """Default contact email (an embedding iframe may override this via the loader)."""
    return os.getenv("WIZARD_CONTACT_EMAIL", "sales@example.com")
