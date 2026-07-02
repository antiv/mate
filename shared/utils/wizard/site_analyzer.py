"""LLM analysis of a crawled website for the Agent Builder Wizard.

Turns the crawled page text into a short business description and a list of services /
appointment reasons, which provisioning injects into the trial agent's description and
instruction (so e.g. a Tier 2 scheduling agent offers concrete booking reasons from a list).

For Tier 3 (sales), ``analyze_products`` performs a second LLM call to extract a product
catalog suitable for the demo shop MCP server.
"""

import json
import logging
import os
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


def _default_model() -> str:
    return (os.getenv("WIZARD_ANALYSIS_MODEL")
            or os.getenv("TITLE_GEN_MODEL")
            or "openrouter/google/gemini-2.5-flash")


def _extract_json(text: str) -> dict:
    """Best-effort: parse the first JSON object in the model's reply."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def analyze_site(pages: List[Dict[str, str]], site_url: str = "", model: str = None) -> dict:
    """Return ``{"description": str, "services": [str, ...]}`` from crawled pages.

    Best-effort: returns ``{}`` (callers fall back to generic defaults) if the LLM is
    unavailable or the reply can't be parsed.
    """
    if not pages:
        return {}

    combined = "\n\n".join(f"# {p.get('title', '')}\n{p.get('text', '')}" for p in pages)
    combined = combined[:12000]
    model = model or _default_model()

    prompt = (
        "You are setting up an AI assistant for a business based on its website.\n"
        "Return ONLY a JSON object with these keys:\n"
        '- "site_name": the short business name as it appears on the site (e.g. "Salon Lena" or "TechMart"). '
        "Keep it brief — just the name, no tagline.\n"
        '- "description": a concise 1-2 sentence description of the business (used as the agent\'s description field).\n'
        '- "services": an array of the concrete services or appointment reasons the business offers '
        '(e.g. ["Haircut","Coloring","Shaving"] or ["Checkup","Repair"]). Use the website\'s language. '
        "If the business clearly has no bookable services, return an empty array.\n"
        '- "working_hours": the business opening hours as a short string if stated on the site '
        '(e.g. "Mon-Fri 09:00-17:00, Sat 09:00-13:00"), else an empty string.\n\n'
        f"Website: {site_url}\n\nContent:\n{combined}"
    )

    try:
        import litellm  # type: ignore
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
        )
        data = _extract_json(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning("Site analysis failed (%s): %s", model, exc)
        return {}

    site_name = (data.get("site_name") or "").strip()
    description = (data.get("description") or "").strip()
    services = data.get("services") or []
    if not isinstance(services, list):
        services = []
    services = [str(s).strip() for s in services if str(s).strip()][:20]
    working_hours = (data.get("working_hours") or "").strip()
    logger.info("Site analysis for %s: name=%r, %d service(s)", site_url, site_name, len(services))
    return {"site_name": site_name, "description": description, "services": services, "working_hours": working_hours}


def analyze_products(pages: List[Dict[str, str]], site_url: str = "", model: str = None) -> List[Dict]:
    """Extract a product/service catalog from crawled pages for the Tier 3 demo shop.

    Returns a list of product dicts compatible with the shop catalog format
    (shop_service.normalize_catalog):
    ``[{"id": str, "name": str, "price": float, "category": str, "description": str}]``

    Best-effort: returns ``[]`` if the LLM is unavailable or nothing useful is found.
    Up to 12 products are returned.
    """
    if not pages:
        return []

    combined = "\n\n".join(f"# {p.get('title', '')}\n{p.get('text', '')}" for p in pages)
    combined = combined[:14000]
    model = model or _default_model()

    prompt = (
        "You are extracting a product or service catalog from a business website for use in a demo e-commerce chatbot.\n"
        "Return ONLY a JSON object with a single key \"products\" containing an array of up to 12 items.\n"
        "Each item must have:\n"
        '  - "id": a short slug (lowercase, hyphens, no spaces), e.g. "red-tshirt"\n'
        '  - "name": the product or service name as shown on the site\n'
        '  - "price": numeric price (float). If no price is visible on the site, estimate a reasonable one.\n'
        '  - "category": one short category word, e.g. "clothing", "electronics", "service", "food"\n'
        '  - "description": one sentence describing the item\n'
        "Include real products/services from the site. If the site sells nothing concrete (e.g. it is a "
        "pure blog or portfolio), return {\"products\": []}.\n"
        "Do NOT invent fictitious brands or products unrelated to the site.\n\n"
        f"Website: {site_url}\n\nContent:\n{combined}"
    )

    try:
        import litellm  # type: ignore
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1200,
        )
        data = _extract_json(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning("Product analysis failed (%s): %s", model, exc)
        return []

    raw = data.get("products") or []
    if not isinstance(raw, list):
        return []

    products = []
    seen_ids = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        # Normalise id
        pid = str(item.get("id") or "").strip()
        if not pid:
            pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        # Normalise price
        try:
            price = round(float(item.get("price") or 0), 2)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            price = 9.99  # placeholder when price is unknown
        products.append({
            "id": pid,
            "name": name,
            "price": price,
            "category": str(item.get("category") or "product").strip().lower(),
            "description": str(item.get("description") or "").strip(),
        })
        if len(products) >= 12:
            break

    logger.info("Product analysis for %s: %d product(s) extracted", site_url, len(products))
    return products
