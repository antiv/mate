"""
Telling someone a request waits in the approvals inbox.

When a request is recorded that the inbox answers (approval_service.inbox_decides),
a message goes to the agent's require_confirmation "notify" destination
(approval_config.py), through the same delivery as alert rules (notify.deliver).
Without one, an agent of the default workspace uses APPROVALS_NOTIFY_TYPE with
APPROVALS_NOTIFY_URL or APPROVALS_NOTIFY_TO; other workspaces set their own, so one
workspace's requests never reach the platform's channel.

The message names the agent, the tool, where the run came from and the names of
the arguments, never their values, which may hold personal data. It links to
/dashboard/approvals?id=<id>, under MATE_PUBLIC_URL when that is set.

Delivery runs in a thread of its own and never fails the run.
"""

import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

from .approval_config import NOTIFY_TYPES

logger = logging.getLogger(__name__)


def default_destination(workspace_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """The APPROVALS_NOTIFY_* destination, for the default workspace only."""
    from .models import DEFAULT_WORKSPACE_ID
    if (workspace_id or DEFAULT_WORKSPACE_ID) != DEFAULT_WORKSPACE_ID:
        return None
    kind = (os.getenv("APPROVALS_NOTIFY_TYPE") or "").strip().lower()
    if kind not in NOTIFY_TYPES:
        return None
    if kind == "email":
        to = (os.getenv("APPROVALS_NOTIFY_TO") or "").strip()
        return {"type": kind, "to": to} if to else None
    url = (os.getenv("APPROVALS_NOTIFY_URL") or "").strip()
    return {"type": kind, "url": url} if url else None


def link(request_id: int) -> str:
    """Where the request opens in the dashboard."""
    path = f"/dashboard/approvals?id={request_id}"
    base = (os.getenv("MATE_PUBLIC_URL") or "").strip().rstrip("/")
    return base + path if base else path


def payload(request: Dict[str, Any]) -> Dict[str, Any]:
    """What is sent: no argument values."""
    arg_names = request.get("arg_names") or []
    message = (f"{request['agent_name']} is waiting for approval to run {request['tool_name']}"
               f" (from {request['source']}).")
    return {
        "event": "approval.requested",
        "message": message,
        "request_id": request["id"],
        "agent": request["agent_name"],
        "tool": request["tool_name"],
        "source": request["source"],
        "arguments": ", ".join(arg_names) if arg_names else "none",
        "expires_at": request.get("expires_at"),
        "link": link(request["id"]),
    }


def send(request: Dict[str, Any], destination: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Send the message for one request; (False, reason) when there is nowhere to send it."""
    from .notify import deliver
    target = destination or default_destination(request.get("workspace_id"))
    if not target:
        return False, "no destination"
    body = payload(request)
    text = f"[MATE approval] {body['message']} Arguments: {body['arguments']}. Open: {body['link']}"
    return deliver(target.get("type"), target, text=text, payload=body,
                   subject=f"[MATE approval] {request['agent_name']}: {request['tool_name']}",
                   workspace_id=request.get("workspace_id"))


def send_in_background(request: Dict[str, Any], destination: Optional[Dict[str, Any]]) -> None:
    def run() -> None:
        try:
            ok, detail = send(request, destination)
            if not ok and detail != "no destination":
                logger.warning("Approval request %s: notification not delivered: %s", request["id"], detail)
        except Exception as e:
            logger.warning("Approval request %s: notification failed: %s", request["id"], e)

    threading.Thread(target=run, daemon=True, name=f"approval-notify-{request['id']}").start()
