"""
Content-Security-Policy for the pages this server sends.

A CSP limits what an injected script can do: load code from another host, send
data to one, or frame the dashboard (#124). There are two policies:

- The *strict* one allows inline <script> blocks only with this request's
  nonce, and no inline on*= handlers. It is what stops an injected script.
- The *legacy* one still has 'unsafe-inline', because pages that have not been
  moved off onclick= handlers yet need it.

CSP_MODE=report-only (the default) sends the strict policy as
Content-Security-Policy-Report-Only, so nothing breaks while violations are
collected. CSP_MODE=enforce enforces the legacy policy and reports against the
strict one, until every page is converted. Browsers post violations to
/csp-report, which logs each distinct one once.

The dashboard may only be framed by itself. The widget chat page is framed by
customer sites, so its frame-ancestors come from the widget key's origin
allowlist (see widget_frame_ancestors).
"""

import json
import logging
import os
import re
import secrets
import time
from typing import Dict, Iterable, List, Optional
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


def new_nonce() -> str:
    """A fresh nonce for one response's inline <script> blocks."""
    return secrets.token_urlsafe(18)


def build_policy(frame_ancestors: str = "'self'", allow_eval: bool = False,
                 nonce: Optional[str] = None) -> str:
    """The legacy policy, or with a nonce the strict one (no 'unsafe-inline' for scripts).

    A nonce and 'unsafe-inline' cannot be combined: browsers ignore
    'unsafe-inline' once a nonce is present, which would break the inline
    handlers the legacy policy exists for.
    """
    extra = extra_sources()
    if nonce is None:
        script_keywords = ["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'"]
    else:
        script_keywords = ["'self'", f"'nonce-{nonce}'", "'wasm-unsafe-eval'"]
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


def widget_frame_ancestors(allowed_origins: Optional[List[str]], strict: bool) -> str:
    """frame-ancestors for the widget chat page, matching what _check_origin lets through.

    No allowlist, or WIDGET_ORIGIN_STRICT off (the allowlist is then only
    logged against), means any site may embed the key. Otherwise the allowlist's
    origins may, and so may MATE itself: the dashboard and the widget admin
    panel preview the widget.
    """
    if allowed_origins is None or not strict:
        return "*"
    if not isinstance(allowed_origins, list):
        return "'self'"  # a malformed allowlist allows no one, as _check_origin does
    sources = ["'self'"]
    for entry in allowed_origins:
        for source in (_origin_to_sources(entry) if isinstance(entry, str) else []):
            if source not in sources:
                sources.append(source)
    return " ".join(sources)


def _origin_to_sources(entry: str) -> List[str]:
    """The CSP sources for an allowlist entry, covering what _origin_matches accepts for it.

    Rebuilt from the entry's parsed parts rather than passed through, since the
    allowlist is written by whoever holds the widget's admin key; an entry that
    is not a plain origin gives none. So does an IPv6 address, which CSP source
    expressions cannot name.
    """
    entry = entry.strip().rstrip("/")
    if entry.startswith("*.") and "://" not in entry:
        # A bare wildcard matches the domain and its subdomains on any scheme and port
        domain = entry[2:].lower()
        if not _is_hostname(domain):
            return []
        return [f"{scheme}://{host}:*" for host in (f"*.{domain}", domain) for scheme in ("https", "http")]
    try:
        parsed = urlparse(entry)
        port = parsed.port
    except ValueError:
        return []
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return []
    if parsed.path or parsed.query or parsed.fragment or parsed.username:
        return []
    host = parsed.hostname.lower()
    wildcard = host.startswith("*.")
    domain = host[2:] if wildcard else host
    if not _is_hostname(domain):
        return []
    suffix = f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else ""
    # https://*.example.com matches example.com itself too
    hosts = [f"*.{domain}", domain] if wildcard else [domain]
    return [f"{parsed.scheme}://{h}{suffix}" for h in hosts]


def _is_hostname(value: str) -> bool:
    return re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", value) is not None


def policy_headers(nonce: str, frame_ancestors: str = "'self'",
                   allow_eval: bool = False) -> Dict[str, str]:
    """The CSP headers for one HTML response, by CSP_MODE."""
    mode = csp_mode()
    if mode == "off":
        return {}
    strict = build_policy(frame_ancestors, allow_eval, nonce=nonce)
    if mode == "report-only":
        return {CSP_REPORT_ONLY_HEADER: strict}
    # Enforce what every page works with today, and report what the strict
    # policy would still block, until no page needs 'unsafe-inline'
    return {CSP_HEADER: build_policy(frame_ancestors, allow_eval),
            CSP_REPORT_ONLY_HEADER: strict}


async def add_csp_header(request: Request, call_next):
    """Middleware: the dashboard policy on every HTML response that has none of its own.

    The nonce is made before the page renders, so templates can put it on their
    inline scripts as {{ request.state.csp_nonce }}.
    """
    request.state.csp_nonce = new_nonce()
    response = await call_next(request)
    if CSP_HEADER in response.headers or CSP_REPORT_ONLY_HEADER in response.headers:
        return response
    if not response.headers.get("content-type", "").startswith("text/html"):
        return response
    if _needs_eval(request.url.path):
        # ADK's dev UI is not our markup: it gets the legacy policy only
        name = header_name()
        if name is not None:
            response.headers[name] = build_policy(allow_eval=True)
        return response
    for name, policy in policy_headers(request.state.csp_nonce).items():
        response.headers[name] = policy
    return response


def _needs_eval(path: str) -> bool:
    """ADK's dev UI (proxied under /dev-ui/) calls new Function() in a bundled library.

    It is ADK's code, not ours, so it gets 'unsafe-eval' on its own pages rather
    than the dashboard losing that protection everywhere, and only while it is
    served at all.
    """
    from shared.utils.utils import adk_dev_ui_enabled

    return (path == "/dev-ui" or path.startswith("/dev-ui/")) and adk_dev_ui_enabled()


def set_csp_header(response: Response, frame_ancestors: str,
                   request: Optional[Request] = None) -> Response:
    """Give a response its own policy, e.g. the widget page with its frame-ancestors.

    Pass the request when the response is a template with inline scripts, so the
    policy carries the nonce they were rendered with.
    """
    nonce = getattr(request.state, "csp_nonce", None) if request is not None else None
    for name, policy in policy_headers(nonce or new_nonce(), frame_ancestors).items():
        response.headers[name] = policy
    return response


# ---------------------------------------------------------------------------
# Violation reports
# ---------------------------------------------------------------------------

report_router = APIRouter(tags=["Security"])

# Reports come unauthenticated from any browser. The body is capped before it is
# read, and logging works in windows: each distinct violation is logged once per
# window, and at most MAX_REPORTS_PER_WINDOW lines are. A budget for the life of
# the process would let a flood of fake reports silence real ones until restart;
# a window mutes them only until it ends, and its end says how many were dropped.
MAX_REPORT_BYTES = 16 * 1024
REPORT_WINDOW_SECONDS = 600
MAX_REPORTS_PER_WINDOW = 100
_seen_reports: set = set()
_window = {"started": 0.0, "dropped": 0}


def _clean(value, limit: int = 300) -> str:
    """One line of report text for the log: no line breaks or control characters, bounded."""
    return re.sub(r"[\x00-\x1f\x7f\x85\u2028\u2029]", " ", str(value or ""))[:limit]


def _text(value) -> str:
    """A report field as text; anything but a string is ignored."""
    return value if isinstance(value, str) else ""


def _strip_query(url: str) -> str:
    """Drop query and fragment: the page URL can carry a widget key or a token."""
    return url.split("?", 1)[0].split("#", 1)[0]


def _start_window_if_due() -> None:
    now = time.monotonic()
    if now - _window["started"] < REPORT_WINDOW_SECONDS:
        return
    if _window["dropped"]:
        logger.warning(f"CSP: {_window['dropped']} more violation report(s) were not logged "
                       f"in the last {REPORT_WINDOW_SECONDS // 60} minutes")
    _seen_reports.clear()
    _window.update(started=now, dropped=0)


async def _read_capped(request: Request) -> Optional[bytes]:
    """The request body, or None once it exceeds MAX_REPORT_BYTES (without reading the rest)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_REPORT_BYTES:
                return None
        except ValueError:
            return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_REPORT_BYTES:
            return None
    return bytes(body)


@report_router.post(REPORT_PATH, include_in_schema=False)
async def csp_report(request: Request) -> Response:
    """Log a CSP violation the browser reports, once per distinct violation and window."""
    body = await _read_capped(request)
    if body is None:
        return Response(status_code=413)
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, RecursionError):
        return Response(status_code=400)
    # report-uri sends {"csp-report": {...}}; the Reporting API sends a list
    reports = payload if isinstance(payload, list) else [payload]
    _start_window_if_due()
    for item in reports:
        if not isinstance(item, dict):
            continue
        report = item.get("csp-report") or item.get("body") or {}
        if not isinstance(report, dict):
            continue
        directive = _text(report.get("effective-directive")) or _text(report.get("effectiveDirective")) \
            or _text(report.get("violated-directive"))
        blocked = _strip_query(_text(report.get("blocked-uri")) or _text(report.get("blockedURL")))
        page = urlparse(_strip_query(_text(report.get("document-uri")) or _text(report.get("documentURL")))).path
        key = (directive, blocked, page)
        if key in _seen_reports:
            continue
        if len(_seen_reports) >= MAX_REPORTS_PER_WINDOW:
            _window["dropped"] += 1
            continue
        _seen_reports.add(key)
        logger.warning(f"CSP violation: {_clean(directive, 60)} blocked "
                       f"'{_clean(blocked)}' on {_clean(page)}")
    return Response(status_code=204)
