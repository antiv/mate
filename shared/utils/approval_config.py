"""
An agent's approval settings: the require_confirmation entry of its tool_config.

Two forms (documents/design/approvals-inbox.md, "Who should answer"):

    "require_confirmation": ["issue_refund"]

    "require_confirmation": {
        "tools": ["issue_refund"],
        "approver": "inbox",
        "notify": {"type": "slack", "url": "https://hooks.slack.com/..."},
        "expires_after_minutes": 1440
    }

The list is the object with only "tools": the chat answers, as before.

- approver: who answers a request from a chat. "chat" (the default) the person
  chatting; "inbox" only an admin in the inbox, and the chat shows no buttons;
  "either" both, and the first answer wins. A run nobody is chatting in is always
  answered in the inbox.
- notify: where to say a request waits for the inbox; the same destinations as
  alert rules (http, email, slack, discord).
- expires_after_minutes: how long a request waits in the inbox before it is
  rejected; APPROVALS_EXPIRE_MINUTES when unset.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

APPROVER_CHAT = "chat"
APPROVER_INBOX = "inbox"
APPROVER_EITHER = "either"
APPROVERS = (APPROVER_CHAT, APPROVER_INBOX, APPROVER_EITHER)

NOTIFY_TYPES = ("http", "email", "slack", "discord")
MAX_EXPIRE_MINUTES = 60 * 24 * 90

KEY = "require_confirmation"
_FIELDS = ("tools", "approver", "notify", "expires_after_minutes")


@dataclass
class ApprovalConfig:
    tools: List[str] = field(default_factory=list)
    approver: str = APPROVER_CHAT
    notify: Optional[Dict[str, Any]] = None
    expires_after_minutes: Optional[int] = None


def _tool_config(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def parse(tool_config: Any) -> ApprovalConfig:
    """
    The approval settings of a tool_config (a dict or its JSON). Never raises:
    a value that doesn't validate is read as far as it can be, with defaults for
    the rest, so an agent saved before validation still loads.
    """
    raw = _tool_config(tool_config).get(KEY)
    if not raw:
        return ApprovalConfig()
    if isinstance(raw, str):
        return ApprovalConfig(tools=[raw])
    if isinstance(raw, list):
        return ApprovalConfig(tools=[t for t in raw if isinstance(t, str) and t])
    if not isinstance(raw, dict):
        return ApprovalConfig()
    tools = raw.get("tools") or []
    if isinstance(tools, str):
        tools = [tools]
    approver = raw.get("approver") if raw.get("approver") in APPROVERS else APPROVER_CHAT
    notify = raw.get("notify") if isinstance(raw.get("notify"), dict) and raw["notify"].get("type") in NOTIFY_TYPES else None
    expires = raw.get("expires_after_minutes")
    expires = expires if isinstance(expires, int) and not isinstance(expires, bool) and 0 < expires <= MAX_EXPIRE_MINUTES else None
    return ApprovalConfig(tools=[t for t in tools if isinstance(t, str) and t] if isinstance(tools, list) else [],
                          approver=approver, notify=notify, expires_after_minutes=expires)


def validate(tool_config: Any) -> List[str]:
    """What is wrong with the require_confirmation entry, for an agent being saved; [] when fine."""
    raw = _tool_config(tool_config).get(KEY)
    if raw is None or raw == [] or isinstance(raw, str):
        return []
    if isinstance(raw, list):
        return [] if all(isinstance(t, str) and t for t in raw) else [
            "require_confirmation: every tool must be a name"]
    if not isinstance(raw, dict):
        return ["require_confirmation must be a list of tool names, or an object"]
    errors = []
    unknown = sorted(set(raw) - set(_FIELDS))
    if unknown:
        errors.append(f"require_confirmation: unknown field(s) {', '.join(unknown)}; "
                      f"expected {', '.join(_FIELDS)}")
    tools = raw.get("tools")
    if not isinstance(tools, list) or not tools or not all(isinstance(t, str) and t for t in tools):
        errors.append("require_confirmation.tools must be a list of tool names")
    if "approver" in raw and raw["approver"] not in APPROVERS:
        errors.append(f"require_confirmation.approver must be one of {', '.join(APPROVERS)}")
    if "expires_after_minutes" in raw and raw["expires_after_minutes"] is not None:
        value = raw["expires_after_minutes"]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= MAX_EXPIRE_MINUTES:
            errors.append(f"require_confirmation.expires_after_minutes must be a whole number "
                          f"from 1 to {MAX_EXPIRE_MINUTES}")
    if "notify" in raw and raw["notify"] is not None:
        notify = raw["notify"]
        if not isinstance(notify, dict) or notify.get("type") not in NOTIFY_TYPES:
            errors.append(f"require_confirmation.notify.type must be one of {', '.join(NOTIFY_TYPES)}")
        elif notify["type"] == "email" and not str(notify.get("to") or "").strip():
            errors.append("require_confirmation.notify.to is required for email")
        elif notify["type"] != "email" and not str(notify.get("url") or "").strip():
            errors.append(f"require_confirmation.notify.url is required for {notify['type']}")
    return errors
