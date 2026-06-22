"""Static pricing / tier configuration for the Agent Builder Wizard.

There is no billing in MATE — these values are display-only. The wizard shows the
estimate and a "contact us" instruction; the chosen estimate is snapshotted onto the lead.

Tier labels/descriptions/features are localized (English + Serbian to start). The canonical
``monthly_estimate`` (top level) is what gets snapshotted onto a lead, regardless of language;
a localized display override can be set per language via ``price``.
"""

import os
from typing import Optional

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "sr")

# Ordered tier catalogue. ``provisionable`` tiers spin up a live trial agent the prospect
# can test; non-provisionable tiers (tier4) only collect a structured request as a lead.
WIZARD_TIERS = [
    {
        "id": "tier1",
        "monthly_estimate": "€49",
        "currency": "EUR",
        "provisionable": True,
        "i18n": {
            "en": {
                "label": "Website Support Agent",
                "description": "An AI agent that reads your website and answers customer support questions on your site, 24/7.",
                "features": [
                    "Reads your website to answer questions",
                    "Custom instructions and tone",
                    "Embeddable chat widget for your site",
                ],
            },
            "sr": {
                "label": "Agent za podršku na sajtu",
                "description": "AI agent koji čita vaš sajt i odgovara na pitanja korisnika na vašem sajtu, 24/7.",
                "features": [
                    "Čita vaš sajt da bi odgovarao na pitanja",
                    "Prilagođene instrukcije i ton",
                    "Chat widget za ugradnju na vaš sajt",
                ],
            },
        },
    },
    {
        "id": "tier2",
        "monthly_estimate": "€99",
        "currency": "EUR",
        "provisionable": True,
        "i18n": {
            "en": {
                "label": "Support + Appointment Scheduling",
                "description": "Everything in Website Support, plus the ability to check availability and book appointments in your calendar.",
                "features": [
                    "Everything in Website Support",
                    "Checks availability in your calendar",
                    "Books and lists appointments",
                ],
            },
            "sr": {
                "label": "Podrška + zakazivanje termina",
                "description": "Sve iz podrške na sajtu, uz proveru slobodnih termina i zakazivanje u vašem kalendaru.",
                "features": [
                    "Sve iz podrške na sajtu",
                    "Proverava slobodne termine u kalendaru",
                    "Zakazuje i prikazuje termine",
                ],
            },
        },
    },
    {
        "id": "tier3",
        "monthly_estimate": "€149",
        "currency": "EUR",
        "provisionable": True,
        "i18n": {
            "en": {
                "label": "Sales Agent",
                "description": "An agent that helps customers find products, fills the cart and places orders through your online store.",
                "features": [
                    "Connects to your store (e.g. Shopify)",
                    "Adds products to the cart",
                    "Creates orders / checkout links",
                ],
            },
            "sr": {
                "label": "Prodajni agent",
                "description": "Agent koji pomaže kupcima da nađu proizvode, puni korpu i šalje porudžbine kroz vašu prodavnicu.",
                "features": [
                    "Povezuje se sa vašom prodavnicom (npr. Shopify)",
                    "Dodaje proizvode u korpu",
                    "Pravi porudžbine / linkove za plaćanje",
                ],
            },
        },
    },
    {
        "id": "tier4",
        "monthly_estimate": "Custom",
        "currency": "EUR",
        "provisionable": False,
        "i18n": {
            "en": {
                "label": "Custom Agent",
                "description": "A fully tailored agent or team of agents built to your requirements. Tell us what you need and we'll design it.",
                "price": "Custom",
                "features": [
                    "Designed to your exact workflow",
                    "Multiple connected tools / systems",
                    "Hands-on setup by our team",
                ],
            },
            "sr": {
                "label": "Agent po meri",
                "description": "Potpuno prilagođen agent ili tim agenata po vašim zahtevima. Recite nam šta vam treba i osmislićemo ga.",
                "price": "Po dogovoru",
                "features": [
                    "Napravljen za vaš tačan tok rada",
                    "Više povezanih alata / sistema",
                    "Postavku radi naš tim",
                ],
            },
        },
    },
]

_TIERS_BY_ID = {t["id"]: t for t in WIZARD_TIERS}


def normalize_lang(lang: Optional[str]) -> str:
    """Map an incoming language code to a supported one (default English)."""
    if not lang:
        return DEFAULT_LANG
    code = lang.split("-")[0].lower()
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def get_tiers(lang: str = DEFAULT_LANG) -> list:
    """Return the tier catalogue localized to ``lang`` (display order preserved)."""
    lang = normalize_lang(lang)
    out = []
    for t in WIZARD_TIERS:
        loc = t["i18n"].get(lang) or t["i18n"][DEFAULT_LANG]
        out.append({
            "id": t["id"],
            "currency": t["currency"],
            "provisionable": t["provisionable"],
            "monthly_estimate": loc.get("price") or t["monthly_estimate"],
            "label": loc["label"],
            "description": loc["description"],
            "features": loc["features"],
        })
    return out


def get_tier(tier_id: str) -> Optional[dict]:
    """Return a single (raw) tier config by id, or None."""
    return _TIERS_BY_ID.get(tier_id)


def get_estimated_price(tier_id: str) -> Optional[str]:
    """Return the canonical (language-independent) price snapshot for a tier, or None."""
    tier = _TIERS_BY_ID.get(tier_id)
    return tier["monthly_estimate"] if tier else None


def get_contact_email() -> str:
    """Default contact email (an embedding iframe may override this via the loader)."""
    return os.getenv("WIZARD_CONTACT_EMAIL", "sales@example.com")
