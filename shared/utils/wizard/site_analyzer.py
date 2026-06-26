"""LLM analysis of a crawled website for the Agent Builder Wizard.

Turns the crawled page text into a short business description and a list of services /
appointment reasons, which provisioning injects into the trial agent's description and
instruction (so e.g. a Tier 2 scheduling agent offers concrete booking reasons from a list).
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
