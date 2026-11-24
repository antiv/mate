"""
New sign-ups waiting to be let in: telling an administrator, and how many there are.

A first single sign-on makes a user with OAUTH_DEFAULT_ROLE, pending unless set, who can
do nothing until an administrator approves them. With NEW_USER_NOTIFY_TYPE, a message
goes to NEW_USER_NOTIFY_URL or NEW_USER_NOTIFY_TO, through the delivery alert rules use.
It is sent in a thread of its own and never fails the sign-in.
"""

import logging
import os
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

PENDING = "pending"


def waiting(session: Any) -> int:
    """How many users wait to be let in."""
    from .models import User
    # LIKE narrows it down; the roles are JSON, so a name that only contains the word is left out
    rows = session.query(User).filter(User.roles.like(f"%{PENDING}%"))
    return sum(PENDING in user.get_roles() for user in rows)


def link() -> str:
    path = "/dashboard/users?role=pending"
    base = (os.getenv("MATE_PUBLIC_URL") or "").strip().rstrip("/")
    return base + path if base else path


def _send(payload: Dict[str, Any]) -> None:
    from .models import DEFAULT_WORKSPACE_ID
    from .notify import deliver, destination_from
    try:
        target = destination_from(os.getenv("NEW_USER_NOTIFY_TYPE"), os.getenv("NEW_USER_NOTIFY_URL"),
                                  os.getenv("NEW_USER_NOTIFY_TO"))
        if not target:
            return
        ok, detail = deliver(target["type"], target, text=f"[MATE] {payload['message']} Open: {payload['link']}",
                             payload=payload, subject="[MATE] A new user is waiting to be let in",
                             workspace_id=DEFAULT_WORKSPACE_ID)
        if not ok:
            logger.warning("New user %s: notification not delivered: %s", payload["user_id"], detail)
    except Exception as e:
        logger.warning("New user %s: notification failed: %s", payload["user_id"], e)


def announce(user_id: str, display_name: str, provider: str) -> None:
    """Tell the administrators' destination, if there is one, that this user waits."""
    name = " ".join(str(display_name or "").split())[:100]
    who = f"{name} ({user_id})" if name and name != user_id else user_id
    payload = {"event": "user.waiting", "message": f"{who} signed in with {provider} and is waiting to be let in.",
               "user_id": user_id, "provider": provider, "link": link()}
    threading.Thread(target=_send, args=(payload,), daemon=True, name="signup-notify").start()
