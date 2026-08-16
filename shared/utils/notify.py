"""
Outbound notification primitives shared by triggers and alert rules.

Both features need "POST this JSON" and "send this email", and the trigger
runner grew its own copies first. These are the single implementation: they
never raise and report success as a flag, so a scheduler thread cannot be taken
down by a dead endpoint. Callers that must surface failure as an exception —
the trigger runner does, since it records the result from a raised error — wrap
them.
"""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT = 30.0
# smtplib inherits the global socket timeout when none is given, which is
# usually "no timeout" — a dead SMTP host would then hang a scheduler worker.
DEFAULT_SMTP_TIMEOUT = 10.0


def post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
              timeout: float = DEFAULT_HTTP_TIMEOUT) -> Tuple[bool, str]:
    """POST a JSON body. Returns (ok, detail)."""
    if not url:
        return False, "missing url"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers or {})
            resp.raise_for_status()
        logger.info("notify: delivered to %s (%s)", url, resp.status_code)
        return True, f"HTTP {resp.status_code}"
    except Exception as e:
        logger.warning("notify: POST to %s failed: %s", url, e)
        return False, f"{type(e).__name__}: {e}"


def send_email(to_addr: str, subject: str, body: str,
               timeout: float = DEFAULT_SMTP_TIMEOUT) -> Tuple[bool, str]:
    """Send a plain-text email over SMTP. Returns (ok, detail)."""
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    from_addr = os.getenv("SMTP_FROM", smtp_user)
    if not to_addr:
        return False, "missing recipient"
    if not smtp_host:
        return False, "SMTP_HOST not configured"
    try:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as smtp:
            smtp.starttls()
            if smtp_user and smtp_pass:
                smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
        logger.info("notify: email sent to %s", to_addr)
        return True, "sent"
    except Exception as e:
        logger.warning("notify: email to %s failed: %s", to_addr, e)
        return False, f"{type(e).__name__}: {e}"
