"""Server-side website crawler for wizard trials.

Reuses the existing Playwright browser session manager from ``browser_tools`` to fetch a
prospect's site (same-domain links up to a configurable depth) and return cleaned page text.
The wizard stores this content into memory blocks so the trial agent can answer immediately,
instead of browsing live on every prompt.
"""

import logging
import os
import time
from typing import Dict, List
from urllib.parse import urldefrag, urlparse

from shared.utils.tools.browser_tools import browser_manager

logger = logging.getLogger(__name__)

_ASSET_SUFFIXES = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".zip", ".rar", ".gz", ".mp4", ".webm", ".mp3", ".wav", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".eot", ".xml", ".json",
)

# Text-extraction JS (mirrors browser_tools._generate_page_summary cleaning).
# Hidden-element removal defends against indirect prompt injection: a malicious site
# could hide "ignore previous instructions" in invisible divs. We strip those before
# extraction so they never reach the agent's memory blocks.
_EXTRACT_TEXT_JS = """() => {
    const el = document.body;
    if (!el) return '';
    const clone = el.cloneNode(true);
    clone.querySelectorAll(
        'script, style, iframe, noscript, svg, nav, footer, ' +
        '[hidden], [aria-hidden="true"], [style*="display:none"], ' +
        '[style*="display: none"], [style*="visibility:hidden"], ' +
        '[style*="visibility: hidden"], [style*="opacity:0"]'
    ).forEach(n => n.remove());
    return clone.innerText || clone.textContent || '';
}"""

_EXTRACT_LINKS_JS = "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"


def _norm_domain(netloc: str) -> str:
    return netloc.lower().split(":")[0].removeprefix("www.")


async def crawl_site(
    start_url: str,
    max_depth: int = None,
    max_pages: int = None,
    session_token: str = "crawl",
) -> List[Dict[str, str]]:
    """Crawl same-domain pages breadth-first and return ``[{url, title, text}]``.

    Best-effort: page-level errors are skipped. Bounded by ``max_pages`` and a time budget.
    """
    max_depth = max_depth if max_depth is not None else int(os.getenv("WIZARD_CRAWL_MAX_DEPTH", "3"))
    max_pages = max_pages if max_pages is not None else int(os.getenv("WIZARD_CRAWL_MAX_PAGES", "10"))
    time_budget_s = int(os.getenv("WIZARD_CRAWL_TIME_BUDGET_S", "90"))
    per_page_timeout_ms = int(os.getenv("WIZARD_CRAWL_PAGE_TIMEOUT_MS", "20000"))

    if not start_url.startswith(("http://", "https://")):
        start_url = "https://" + start_url
    base_domain = _norm_domain(urlparse(start_url).netloc)

    session_browser = await browser_manager.get_session("wizard_crawler", session_token)
    page = await session_browser.get_page()

    visited = set()
    results: List[Dict[str, str]] = []
    queue = [(start_url, 0)]
    started = time.time()

    try:
        while queue and len(results) < max_pages and (time.time() - started) < time_budget_s:
            url, depth = queue.pop(0)
            url, _ = urldefrag(url)
            if url in visited:
                continue
            visited.add(url)

            try:
                await page.goto(url, wait_until="load", timeout=per_page_timeout_ms)
                await page.wait_for_timeout(500)
                title = await page.title()
                text = await page.evaluate(_EXTRACT_TEXT_JS)
                links = await page.evaluate(_EXTRACT_LINKS_JS) if depth < max_depth else []
            except Exception as exc:
                logger.warning("Crawl skipped %s: %s", url, exc)
                continue

            text = "\n".join(ln.strip() for ln in (text or "").split("\n") if ln.strip())
            if text:
                results.append({
                    "url": url,
                    "title": (title or url).strip(),
                    "text": text[:6000],
                })

            for link in links:
                link, _ = urldefrag(link)
                parsed = urlparse(link)
                if parsed.scheme not in ("http", "https"):
                    continue
                if _norm_domain(parsed.netloc) != base_domain:
                    continue
                if link.lower().endswith(_ASSET_SUFFIXES):
                    continue
                if link not in visited:
                    queue.append((link, depth + 1))
    finally:
        try:
            await browser_manager.close_session("wizard_crawler", session_token)
        except Exception:
            pass

    logger.info("Crawl of %s collected %d page(s)", start_url, len(results))
    return results
