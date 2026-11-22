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


# Chat incoming webhooks take a fixed body, not MATE's payload: Slack rejects a
# post without `text` and Discord one without `content`.
CHAT_MESSAGE_KEYS = {'slack': 'text', 'discord': 'content'}
DESTINATION_TYPES = ('http', 'email', 'slack', 'discord')


def may_email(workspace_id: Optional[int]) -> bool:
    """True if the workspace may send email through the server's SMTP account
    (platform_only.FEATURE_EMAIL). No workspace means the default one."""
    from .database_client import get_database_client
    from .models import DEFAULT_WORKSPACE_ID
    from .platform_only import FEATURE_EMAIL
    from .workspaces import workspace_has_feature
    db = get_database_client()
    session = db.get_session() if db else None
    if not session:
        return False
    try:
        return workspace_has_feature(session, workspace_id or DEFAULT_WORKSPACE_ID, FEATURE_EMAIL)
    finally:
        session.close()


def deliver(destination_type: str, config: Dict[str, Any], *, text: str, payload: Dict[str, Any],
            subject: str, workspace_id: Optional[int]) -> Tuple[bool, str]:
    """
    Send one message to a destination of an alert rule or an agent's approvals:

    - http: payload as JSON, with config's headers;
    - email: subject (config's own wins) and text, then payload's other fields;
    - slack, discord: text, in the body their incoming webhooks take.

    A URL the workspace may not reach, or email for a workspace without it, is
    refused. Returns (ok, detail); never raises.
    """
    config = config or {}
    if destination_type == 'http' or destination_type in CHAT_MESSAGE_KEYS:
        from .outbound import outbound_refusal
        refusal = outbound_refusal(config.get('url', ''), workspace_id=workspace_id)
        if refusal:
            return False, f"refused: {refusal}"
    timeout = float(config.get('timeout', DEFAULT_HTTP_TIMEOUT))
    if destination_type == 'http':
        return post_json(config.get('url', ''), payload, headers=config.get('headers') or {},
                         timeout=timeout)
    if destination_type == 'email':
        if not may_email(workspace_id):
            return False, ("refused: a platform administrator hasn't enabled email for "
                           "the workspace")
        body = text + "\n\n" + "\n".join(
            f"{k}: {v}" for k, v in payload.items() if k != 'message')
        return send_email(config.get('to', ''), config.get('subject') or subject, body)
    if destination_type in CHAT_MESSAGE_KEYS:
        if destination_type == 'slack':
            # Slack reads <...> as links and mentions (<!channel>); these three are its escapes
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        body: Dict[str, Any] = {CHAT_MESSAGE_KEYS[destination_type]: text}
        if destination_type == 'discord':
            body['allowed_mentions'] = {'parse': []}  # no @everyone from a name in the text
        return post_json(config.get('url', ''), body, timeout=timeout)
    return False, f"unknown destination_type '{destination_type}'"
