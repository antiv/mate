"""
Forward a standalone build's ratings to a central MATE.

A standalone build has no dashboard, so a 👍/👎 is only worth collecting if it
reaches one. With MATE_FEEDBACK_URL and MATE_FEEDBACK_KEY set, the build's
/feedback route sends each rating to that MATE's /widget/api/feedback under a
widget key for the same agent. The central server cannot read the build's
sessions, so the rated question and answer go with it, read here from the
build's own session store, never taken from the browser.
"""

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

import httpx

from .feedback_service import MAX_COMMENT, RATINGS, extract_exchange

logger = logging.getLogger(__name__)

FORWARD_TIMEOUT = 10.0


def feedback_target() -> Optional[Tuple[str, str]]:
    """(central MATE URL, widget key), or None when forwarding is not configured."""
    url = (os.getenv("MATE_FEEDBACK_URL") or "").strip().rstrip("/")
    key = (os.getenv("MATE_FEEDBACK_KEY") or "").strip()
    return (url, key) if url and key else None


class RateLimiter:
    """At most `limit` calls per `window` seconds per key, in memory.

    The standalone chat has no login, so this is what stands between an open
    /feedback route and a flood of requests to the central server.
    """

    def __init__(self, limit: int = 30, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def parse_rating(body: Any) -> Dict[str, Any]:
    """The fields a forwarded rating needs, or ValueError with what is wrong."""
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    fields = {k: body.get(k) for k in ("session_id", "message_id", "user_id", "rating")}
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
        fields[name] = value.strip()
    if fields["rating"] not in RATINGS:
        raise ValueError("rating must be 'up' or 'down'")
    comment = body.get("comment")
    fields["comment"] = comment[:MAX_COMMENT] if isinstance(comment, str) else None
    return fields


async def read_exchange(session_service, app_name: str, user_id: str, session_id: str,
                        invocation_id: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """The rated question and answer from the build's session; None if there is no such session."""
    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id)
    if session is None:
        return None
    events = [e.model_dump(mode="json", exclude_none=True) for e in session.events]
    return extract_exchange(events, invocation_id)


async def forward(url: str, key: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
    """POST one rating to the central MATE. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=FORWARD_TIMEOUT) as client:
            resp = await client.post(f"{url}/widget/api/feedback", json=payload,
                                     headers={"X-Widget-Key": key})
        if resp.status_code >= 400:
            logger.warning("Feedback forward to %s refused: HTTP %s", url, resp.status_code)
            return False, f"HTTP {resp.status_code}"
        return True, f"HTTP {resp.status_code}"
    except Exception as e:
        logger.warning("Feedback forward to %s failed: %s", url, e)
        return False, type(e).__name__
