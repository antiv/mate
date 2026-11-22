"""
Approval requests: the tool calls an agent paused on for approval (require_confirmation).

Both runtimes call this module where they see the pause and the answer, so a row
looks the same whichever runtime wrote it (documents/design/approvals-inbox.md):

- ADK: ApprovalCapturePlugin (shared/callbacks/approval_plugin.py), registered on
  every App, sees every surface, including A2A and MCP calls that never reach the
  auth server.
- LangGraph: the executor (shared/utils/langgraph/executor.py).

Phase 1 only records. A row is decided when the answer comes back in the same
session, which today means a chat; nothing here changes how a run goes. Every
function returns quietly on failure, because it runs beside a live response.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from .database_client import get_database_client
from .models import ApprovalRequest

logger = logging.getLogger(__name__)

CONFIRMATION_CALL_NAME = "adk_request_confirmation"
# Session state key under which the trigger runner names the trigger that started a run
TRIGGER_STATE_KEY = "mate_trigger_id"
# Arguments may hold personal data or secrets; the row keeps at most this much of them
MAX_ARGS_CHARS = 16_000

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
STATUSES = (PENDING, APPROVED, REJECTED, "expired", "cancelled")

RESOURCE_TYPE = "approval_request"


def _get(mapping: Any, *keys: str) -> Any:
    """The first of keys present in mapping: event args come in camelCase or snake_case."""
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def parse_confirmation_call(call_id: Optional[str], args: Any) -> Optional[Dict[str, Any]]:
    """
    What an adk_request_confirmation call asks for, or None when it names no tool.

    ADK writes args as {"originalFunctionCall": {...}, "toolConfirmation": {...}};
    the LangGraph runtime emits the same shape. snake_case is accepted too.
    """
    original = _get(args, "originalFunctionCall", "original_function_call")
    confirmation = _get(args, "toolConfirmation", "tool_confirmation") or {}
    tool_name = _get(original, "name")
    if not call_id or not tool_name:
        return None
    return {
        "confirmation_call_id": str(call_id),
        "tool_call_id": _get(original, "id"),
        "tool_name": str(tool_name),
        "tool_args": _get(original, "args"),
        "hint": _get(confirmation, "hint") or None,
    }


def serialize_args(tool_args: Any) -> Optional[str]:
    """The arguments as stored: JSON, cut at MAX_ARGS_CHARS (then no longer valid JSON)."""
    if tool_args is None:
        return None
    try:
        text = json.dumps(tool_args, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = str(tool_args)
    if len(text) > MAX_ARGS_CHARS:
        return text[:MAX_ARGS_CHARS] + "…"
    return text


def args_digest(stored_args: Optional[str]) -> Optional[str]:
    """SHA-256 of the arguments as stored; the audit log records this, not the values."""
    if stored_args is None:
        return None
    return hashlib.sha256(stored_args.encode("utf-8")).hexdigest()


def _session():
    try:
        db = get_database_client()
        if not db or not db.is_connected():
            return None
        return db.get_session()
    except Exception as e:
        logger.warning("Approval requests unavailable: %s", e)
        return None


def record_request(*, confirmation_call_id: Optional[str], args: Any, agent_name: Optional[str],
                   app_name: str, user_id: str, session_id: str,
                   invocation_id: Optional[str] = None,
                   trigger_id: Any = None) -> Optional[int]:
    """
    Write a pending row for a confirmation call; returns its id.

    Writing the same call twice (same session and confirmation id) leaves the first
    row and returns its id.
    """
    parsed = parse_confirmation_call(confirmation_call_id, args)
    if not parsed:
        return None
    session = _session()
    if not session:
        return None
    try:
        from .log_projects import scope_of_agent
        from .response_metrics import resolve_origin
        agent = agent_name or app_name
        scope = scope_of_agent(session, agent)
        if scope["project_id"] is None and agent != app_name:
            # An agent the database doesn't know (a hardcoded sub-agent): its root's scope
            scope = scope_of_agent(session, app_name)
        try:
            trigger = int(trigger_id) if trigger_id is not None else None
        except (TypeError, ValueError):
            trigger = None
        row = ApprovalRequest(
            status=PENDING,
            agent_name=agent,
            project_id=scope["project_id"],
            workspace_id=scope["workspace_id"],
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            confirmation_call_id=parsed["confirmation_call_id"],
            tool_call_id=parsed["tool_call_id"],
            tool_name=parsed["tool_name"],
            tool_args=serialize_args(parsed["tool_args"]),
            hint=parsed["hint"],
            source=resolve_origin(user_id, session_id),
            trigger_id=trigger,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = (session.query(ApprovalRequest.id)
                        .filter_by(session_id=session_id,
                                   confirmation_call_id=parsed["confirmation_call_id"])
                        .scalar())
            return existing
        return row.id
    except Exception as e:
        logger.warning("Approval request not recorded: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return None
    finally:
        session.close()


def record_decision(*, session_id: str, confirmation_call_id: str, confirmed: bool,
                    decided_by: Optional[str], via: str = "chat") -> bool:
    """
    Mark a pending row approved or rejected, and audit it; False when nothing changed.

    The update only takes a row that is still pending, so a decision made twice (two
    clicks, or the inbox and the chat) is recorded and audited once.
    """
    session = _session()
    if not session:
        return False
    try:
        status = APPROVED if confirmed else REJECTED
        result = session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.session_id == session_id,
                   ApprovalRequest.confirmation_call_id == confirmation_call_id,
                   ApprovalRequest.status == PENDING)
            .values(status=status, decided_by=decided_by or "unknown", decided_via=via,
                    decided_at=datetime.now(timezone.utc)))
        session.commit()
        if result.rowcount != 1:
            return False
        row = (session.query(ApprovalRequest)
               .filter_by(session_id=session_id, confirmation_call_id=confirmation_call_id)
               .one())
        from . import audit_service
        audit_service.log(
            actor=row.decided_by,
            action=f"approval.{status}",
            resource_type=RESOURCE_TYPE,
            resource_id=str(row.id),
            details={
                "agent": row.agent_name,
                "tool": row.tool_name,
                "source": row.source,
                "decided_via": via,
                "args_sha256": args_digest(row.tool_args),
            },
            project_id=row.project_id,
            workspace_id=row.workspace_id,
        )
        return True
    except Exception as e:
        logger.warning("Approval decision not recorded: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return False
    finally:
        session.close()


def confirmation_calls(parts: Iterable[Any]) -> Iterable[Dict[str, Any]]:
    """The adk_request_confirmation calls among wire-shaped parts, as {"id", "args"}."""
    for part in parts or ():
        if not isinstance(part, dict):
            continue
        call = part.get("functionCall") or part.get("function_call")
        if isinstance(call, dict) and call.get("name") == CONFIRMATION_CALL_NAME:
            yield {"id": call.get("id"), "args": call.get("args")}
