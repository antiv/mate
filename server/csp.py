"""
Content-Security-Policy for the pages this server sends.

A CSP limits what an injected script can do: load code from another host, send
data to one, or frame the dashboard. This is the first step of #124. The
policy restricts *where* scripts, styles and connections come from, but keeps
'unsafe-inline': the templates still have inline <script> blocks and hundreds
of onclick= handlers.

It is sent as Content-Security-Policy-Report-Only by default (CSP_MODE), so
nothing breaks while violations are collected. Browsers post them to
/csp-report, which logs each distinct one once.

The dashboard may only be framed by itself. The widget chat page is framed by
customer sites, so its frame-ancestors come from the widget key's origin
allowlist (see widget_frame_ancestors).
"""

import json
import logging
import os
import re
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

CSP_HEADER = "Content-Security-Policy"
CSP_REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"
REPORT_PATH = "/csp-report"

# The CDNs the templates load from. Keep in step with the templates: a host
# missing here shows up as a violation report, a host left here after its last
# use is an unneeded opening.
_SCRIPT_HOSTS = [
    "https://cdn.tailwindcss.com",
    "https://cdn.jsdelivr.net",    # Chart.js, Monaco, React Flow, Pyodide, Swagger UI
    "https://cdnjs.cloudflare.com",  # Ace
    "https://unpkg.com",           # d3-graphviz (graph view)
    "https://d3js.org",
]
_STYLE_HOSTS = [
    "https://cdn.jsdelivr.net",
    "https://cdnjs.cloudflare.com",  # Font Awesome
    "https://fonts.googleapis.com",
]
_FONT_HOSTS = [
    "https://cdn.jsdelivr.net",
    "https://cdnjs.cloudflare.com",
    "https://fonts.gstatic.com",
]
# Pyodide fetches its wasm and packages; Monaco and Ace fetch their own modules.
_CONNECT_HOSTS = ["https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"]
_FRAME_HOSTS = ["https://dartpad.dev"]  # Work Room's Dart runner

# A source expression is one token: anything that could end the directive or
# start another (";", ",", whitespace, quotes) is refused rather than escaped.
_SOURCE_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?(?:\*\.)?[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[^\s;,'\"]*)?$")


def csp_mode() -> str:
    """'report-only' (default), 'enforce' or 'off', from CSP_MODE."""
    mode = os.getenv("CSP_MODE", "report-only").strip().lower()
    return mode if mode in ("report-only", "enforce", "off") else "report-only"


def header_name() -> Optional[str]:
    mode = csp_mode()
    if mode == "off":
        return None
    return CSP_HEADER if mode == "enforce" else CSP_REPORT_ONLY_HEADER


def extra_sources() -> List[str]:
    """Hosts a deployment adds with CSP_EXTRA_SOURCES (comma or space separated)."""
    raw = os.getenv("CSP_EXTRA_SOURCES", "")
    sources = []
    for token in re.split(r"[\s,]+", raw):
        if not token:
            continue
        if _SOURCE_RE.match(token):
            sources.append(token)
        else:
            logger.warning(f"CSP_EXTRA_SOURCES: ignoring '{token}', not a host source")
    return sources


def build_policy(frame_ancestors: str = "'self'", allow_eval: bool = False) -> str:
    extra = extra_sources()
    script_keywords = ["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'"]
    if allow_eval:
        script_keywords.append("'unsafe-eval'")

    def directive(name: str, *sources: Iterable[str]) -> str:
        values: List[str] = []
        for group in sources:
            for source in group:
                if source not in values:
                    values.append(source)
        return f"{name} {' '.join(values)}"

    directives = [
        "default-src 'self'",
        # 'wasm-unsafe-eval' lets Pyodide and the graph view compile WebAssembly;
        # it does not allow eval() of JavaScript.
        directive("script-src", script_keywords, _SCRIPT_HOSTS, extra),
        directive("style-src", ["'self'", "'unsafe-inline'"], _STYLE_HOSTS, extra),
        directive("font-src", ["'self'", "data:"], _FONT_HOSTS, extra),
        # Images stay open: answers render images from anywhere, and an image
        # cannot run code. Tightening this is a later step.
        "img-src 'self' data: blob: https:",
        "media-src 'self' data: blob:",
        directive("connect-src", ["'self'"], _CONNECT_HOSTS, extra),
        # Monaco, Ace and Pyodide start their workers from blob: URLs
        "worker-src 'self' blob:",
        directive("frame-src", ["'self'"], _FRAME_HOSTS, extra),
        "object-src 'none'",
        "base-uri 'self'",
        f"frame-ancestors {frame_ancestors}",
        f"report-uri {REPORT_PATH}",
    ]
    return "; ".join(directives)


def widget_frame_ancestors(allowed_origins: Optional[List[str]]) -> str:
    """frame-ancestors for the widget chat page, from the key's origin allowlist.

    No allowlist means the key may be embedded anywhere. 'self' is always
    allowed: the dashboard and the widget admin panel preview the widget.
    """
    if allowed_origins is None:
        return "*"
    if not isinstance(allowed_origins, list):
        return "'self'"  # a malformed allowlist allows no one, as _check_origin does
    sources = ["'self'"]
    for entry in allowed_origins:
        source = _origin_to_source(entry)
        if source and source not in sources:
            sources.append(source)
    return " ".join(sources)


def _origin_to_source(entry: str) -> Optional[str]:
    """An allowlist entry as a CSP source, or None when it is not a plain origin.

    Rebuilt from its parsed parts rather than passed through, since the
    allowlist is written by whoever holds the widget's admin key.
    """
    entry = (entry or "").strip().rstrip("/")
    if entry.startswith("*.") and "://" not in entry:
        host, scheme, port = entry, None, None
    else:
        try:
            parsed = urlparse(entry)
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.path or parsed.query or parsed.fragment or parsed.username:
            return None
        host, scheme = parsed.hostname, parsed.scheme
    if not re.fullmatch(r"(?:\*\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*", host.lower()):
        return None
    source = host.lower()
    if scheme:
        source = f"{scheme}://{source}"
    if port and port != {"http": 80, "https": 443}.get(scheme):
        source = f"{source}:{port}"
    return source


async def add_csp_header(request: Request, call_next):
    """Middleware: the dashboard policy on every HTML response that has none of its own."""
    response = await call_next(request)
    name = header_name()
    if name is None:
        return response
    if CSP_HEADER in response.headers or CSP_REPORT_ONLY_HEADER in response.headers:
        return response
    if not response.headers.get("content-type", "").startswith("text/html"):
        return response
    response.headers[name] = build_policy(allow_eval=_needs_eval(request.url.path))
    return response


def _needs_eval(path: str) -> bool:
    """ADK's dev UI (proxied under /dev-ui/) calls new Function() in a bundled library.

    It is ADK's code, not ours, so it gets 'unsafe-eval' on its own pages rather
    than the dashboard losing that protection everywhere, and only while it is
    served at all.
    """
    from shared.utils.utils import adk_dev_ui_enabled

    return (path == "/dev-ui" or path.startswith("/dev-ui/")) and adk_dev_ui_enabled()


def set_csp_header(response: Response, frame_ancestors: str) -> Response:
    """Give a response its own policy, e.g. the widget page with its frame-ancestors."""
    name = header_name()
    if name is not None:
        response.headers[name] = build_policy(frame_ancestors)
    return response


# ---------------------------------------------------------------------------
# Violation reports
# ---------------------------------------------------------------------------

report_router = APIRouter(tags=["Security"])

# Reports come unauthenticated from any browser, so the body and the number of
# distinct reports logged are both capped.
MAX_REPORT_BYTES = 16 * 1024
MAX_DISTINCT_REPORTS = 500
_seen_reports: set = set()


def _clean(value, limit: int = 300) -> str:
    """One line of report text for the log: no control characters, bounded."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:limit]


def _strip_query(url: str) -> str:
    """Drop query and fragment: the page URL can carry a widget key or a token."""
    return str(url or "").split("?", 1)[0].split("#", 1)[0]


@report_router.post(REPORT_PATH, include_in_schema=False)
async def csp_report(request: Request) -> Response:
    """Log a CSP violation the browser reports, once per distinct violation."""
    body = await request.body()
    if len(body) > MAX_REPORT_BYTES:
        return Response(status_code=413)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return Response(status_code=400)
    # report-uri sends {"csp-report": {...}}; the Reporting API sends a list
    reports = payload if isinstance(payload, list) else [payload]
    for item in reports:
        if not isinstance(item, dict):
            continue
        report = item.get("csp-report") or item.get("body") or {}
        if not isinstance(report, dict):
            continue
        directive = report.get("effective-directive") or report.get("effectiveDirective") \
            or report.get("violated-directive") or ""
        blocked = _strip_query(report.get("blocked-uri") or report.get("blockedURL") or "")
        page = urlparse(_strip_query(report.get("document-uri") or report.get("documentURL") or "")).path
        key = (directive, blocked, page)
        if key in _seen_reports:
            continue
        if len(_seen_reports) >= MAX_DISTINCT_REPORTS:
            if len(_seen_reports) == MAX_DISTINCT_REPORTS:
                _seen_reports.add(None)
                logger.warning(f"CSP: {MAX_DISTINCT_REPORTS} distinct violations logged; "
                               "ignoring further ones until restart")
            continue
        _seen_reports.add(key)
        logger.warning(f"CSP violation: {_clean(directive, 60)} blocked "
                       f"'{_clean(blocked)}' on {_clean(page)}")
    return Response(status_code=204)
