"""
Approval requests: the tool calls an agent paused on for approval (require_confirmation).

Both runtimes call this module where they see the pause and the answer, so a row
looks the same whichever runtime wrote it (documents/design/approvals-inbox.md):

- ADK: ApprovalCapturePlugin (shared/callbacks/approval_plugin.py), registered on
  every App, sees every surface, including A2A and MCP calls that never reach the
  auth server.
- LangGraph: the executor (shared/utils/langgraph/executor.py).

A row is decided by the answer coming back in the same session (a chat), or in
the inbox (claim_decision), which then resumes the run (approval_resume.py). Who
may answer is the agent's approver setting (approval_config.py); a run with nobody
in a chat (source other than chat) can only be answered in the inbox. A request
the inbox answers expires after the agent's expires_after_minutes, else
APPROVALS_EXPIRE_MINUTES, and is announced to its notify destination
(approval_notify.py).

Every answer on its way to a run passes gate_answer, so a request is answered at
most once, and only as decided: a chat's late or forged answer never runs a tool
the inbox rejected, nor runs it a second time. The functions that run beside a
live response return quietly on failure.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from .database_client import get_database_client
from .approval_config import APPROVER_CHAT, APPROVER_EITHER, APPROVER_INBOX, ApprovalConfig, parse
from .models import AgentConfig, ApprovalRequest

logger = logging.getLogger(__name__)

CONFIRMATION_CALL_NAME = "adk_request_confirmation"
# Session state key under which the trigger runner names the trigger that started a run
TRIGGER_STATE_KEY = "mate_trigger_id"
# Arguments may hold personal data or secrets; the row keeps at most this much of them
MAX_ARGS_CHARS = 16_000

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
CANCELLED = "cancelled"
STATUSES = (PENDING, APPROVED, REJECTED, EXPIRED, CANCELLED)

# Where the run came from. Only a chat has someone to answer in it; every other
# source is answered in the inbox.
SOURCE_CHAT = "chat"
SOURCES = (SOURCE_CHAT, "api", "trigger", "slack", "eval", "a2a", "mcp")

# resume_status while the run is being resumed, and after
RESUMING = "resuming"
RESUME_OK = "ok"
RESUME_ERROR = "error"
MAX_RESULT_CHARS = 4_000

DEFAULT_EXPIRE_MINUTES = 24 * 60

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


def answer_confirmed(response: Any) -> bool:
    """
    Whether an answer to a confirmation call approves, read as ADK reads it
    (ToolConfirmation.from_response_dict): {"confirmed": ...}, or that wrapped as
    {"response": <dict or JSON text>}, with pydantic's reading of booleans, so
    "false" is no. An answer it can't read is a rejection.
    """
    try:
        from google.adk.tools.tool_confirmation import ToolConfirmation
        return bool(ToolConfirmation.from_response_dict(response if isinstance(response, dict) else {}).confirmed)
    except Exception:
        return False


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


def chat_may_answer(source: Optional[str], approver: Optional[str]) -> bool:
    """Whether the person chatting may answer: a chat's request, unless the agent sends it to the inbox."""
    return (source or SOURCE_CHAT) == SOURCE_CHAT and (approver or APPROVER_CHAT) != APPROVER_INBOX


def inbox_decides(source: Optional[str], approver: Optional[str]) -> bool:
    """Whether the inbox may answer: a run nobody chats in, or an agent that sends its requests there."""
    return (source or SOURCE_CHAT) != SOURCE_CHAT or (approver or APPROVER_CHAT) != APPROVER_CHAT


def inbox_clause() -> Any:
    """inbox_decides as a filter on ApprovalRequest."""
    return or_(ApprovalRequest.source != SOURCE_CHAT,
               ApprovalRequest.approver.in_([APPROVER_INBOX, APPROVER_EITHER]))


def answered_in(source: Optional[str], approver: Optional[str]) -> str:
    """What a chat shows for a request: "chat" (buttons), "inbox" (none) or "either" (both)."""
    if not chat_may_answer(source, approver):
        return APPROVER_INBOX
    return APPROVER_EITHER if (approver or APPROVER_CHAT) == APPROVER_EITHER else APPROVER_CHAT


def _approval_config(session: Any, *names: str) -> ApprovalConfig:
    """The approval settings of the first of names the database knows (an agent, then its root)."""
    for name in names:
        if not name:
            continue
        found = session.query(AgentConfig.tool_config).filter(AgentConfig.name == name).first()
        if found is not None:
            return parse(found[0])
    return ApprovalConfig()


def _session_source(session: Any, session_id: str) -> Optional[str]:
    """
    The source of a session's earlier requests when it was A2A or MCP. The inbox
    resumes such a run over /run_sse, which has no /a2a/ surface, and its user and
    session ids say nothing of where it came from.
    """
    found = (session.query(ApprovalRequest.source)
             .filter(ApprovalRequest.session_id == session_id,
                     ApprovalRequest.source.in_(("a2a", "mcp")))
             .order_by(ApprovalRequest.id.desc()).first())
    return found[0] if found else None


def expire_minutes() -> int:
    """How long a request answered in the inbox waits before it expires (APPROVALS_EXPIRE_MINUTES)."""
    try:
        return max(1, int(os.getenv("APPROVALS_EXPIRE_MINUTES", str(DEFAULT_EXPIRE_MINUTES))))
    except ValueError:
        return DEFAULT_EXPIRE_MINUTES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session() -> Optional[Any]:
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
                   trigger_id: Any = None, source: Optional[str] = None) -> Optional[int]:
    """
    Write a pending row for a confirmation call; returns its id.

    Writing the same call twice (same session and confirmation id) leaves the first
    row and returns its id. source overrides what the user and session ids say
    (A2A and MCP runs, which carry no marker of their own in either).
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
        config = _approval_config(session, agent, app_name)
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
            source=source or _session_source(session, session_id) or resolve_origin(user_id, session_id),
            trigger_id=trigger,
            approver=config.approver,
            expires_at=_now() + timedelta(minutes=config.expires_after_minutes or expire_minutes()),
        )
        session.add(row)
        try:
            session.commit()
            if inbox_decides(row.source, row.approver):
                _announce(row, config)
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


def _announce(row: ApprovalRequest, config: ApprovalConfig) -> None:
    """Send the notification for a request the inbox answers; never raises."""
    try:
        from . import approval_notify
        args = row.get_tool_args() if hasattr(row, "get_tool_args") else None
        request = {
            "id": row.id, "agent_name": row.agent_name, "tool_name": row.tool_name,
            "source": row.source, "workspace_id": row.workspace_id,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "arg_names": sorted(args) if isinstance(args, dict) else [],
        }
        approval_notify.send_in_background(request, config.notify)
    except Exception as e:
        logger.warning("Approval request %s: notification not started: %s", row.id, e)


def answering(request_id: Optional[int]) -> str:
    """answered_in for a recorded request; "chat" when there is none."""
    session = _session() if request_id else None
    if not session:
        return APPROVER_CHAT
    try:
        row = session.get(ApprovalRequest, request_id)
        return answered_in(row.source, row.approver) if row else APPROVER_CHAT
    except Exception as e:
        logger.debug("Approval request lookup failed: %s", e)
        return APPROVER_CHAT
    finally:
        session.close()


def gate_answer(*, session_id: str, confirmation_call_id: str, confirmed: bool,
                answered_by: Optional[str]) -> Optional[bool]:
    """
    The answer a run may receive for a confirmation call, or None when it may receive none.

    - No row, or no database to read it from: None. Without the row nobody can
      tell whether the chat may answer, so the check fails closed.
    - Pending, and the chat may answer (chat_may_answer): decided by this answer,
      as record_decision, and passed on.
    - Pending otherwise: the inbox answers it, so the chat's answer is dropped.
    - Decided (approved, rejected, expired) and not yet answered: the decision,
      whatever the answer said; this is how the inbox's resume reaches the run.
    - Answered already, or cancelled: None.
    """
    session = _session()
    if not session:
        logger.warning("Approval answer dropped: no database to check it against")
        return None
    try:
        row = (session.query(ApprovalRequest)
               .filter_by(session_id=session_id, confirmation_call_id=confirmation_call_id).first())
        if not row:
            logger.warning("Approval answer dropped: no request recorded for call %s", confirmation_call_id)
            return None
        source, approver, status = row.source, row.approver, row.status
        request_id = row.id
    except Exception as e:
        logger.warning("Approval answer dropped: the request could not be read: %s", e)
        return None
    finally:
        session.close()

    if status == PENDING:
        if not chat_may_answer(source, approver):
            logger.info("Approval request %s: the chat's answer dropped; the inbox answers it", request_id)
            return None
        if record_decision(session_id=session_id, confirmation_call_id=confirmation_call_id,
                           confirmed=confirmed, decided_by=answered_by, via="chat"):
            return confirmed
        # Decided meanwhile, in the inbox: what it decided, if not yet sent
    return _claim_answer(request_id)


def _claim_answer(request_id: int) -> Optional[bool]:
    """Mark a decided request answered, once; its decision, or None."""
    session = _session()
    if not session:
        return None
    try:
        result = session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == request_id, ApprovalRequest.answered_at.is_(None),
                   ApprovalRequest.status.in_([APPROVED, REJECTED, EXPIRED]))
            .values(answered_at=_now()))
        session.commit()
        if result.rowcount != 1:
            logger.info("Approval request %s: a second answer dropped", request_id)
            return None
        status = session.query(ApprovalRequest.status).filter(ApprovalRequest.id == request_id).scalar()
        return status == APPROVED
    except Exception as e:
        logger.warning("Approval answer not claimed: %s", e)
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
                    decided_at=datetime.now(timezone.utc), answered_at=datetime.now(timezone.utc)))
        session.commit()
        if result.rowcount != 1:
            return False
        row = (session.query(ApprovalRequest)
               .filter_by(session_id=session_id, confirmation_call_id=confirmation_call_id)
               .one())
        _audit(row, status, via)
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


def _audit(row: ApprovalRequest, status: str, via: str, **extra: Any) -> None:
    from . import audit_service
    details = {
        "agent": row.agent_name,
        "tool": row.tool_name,
        "source": row.source,
        "decided_via": via,
        "args_sha256": args_digest(row.tool_args),
    }
    details.update({k: v for k, v in extra.items() if v is not None})
    audit_service.log(
        actor=row.decided_by or "system",
        action=f"approval.{status}",
        resource_type=RESOURCE_TYPE,
        resource_id=str(row.id),
        details=details,
        project_id=row.project_id,
        workspace_id=row.workspace_id,
    )


def snapshot(row: ApprovalRequest) -> Dict[str, Any]:
    """What resuming needs, read before the database session closes."""
    return {"id": row.id, "app_name": row.app_name, "user_id": row.user_id,
            "session_id": row.session_id, "confirmation_call_id": row.confirmation_call_id,
            "source": row.source, "trigger_id": row.trigger_id, "status": row.status}


DECISIONS = {"approve": True, "reject": False}


def claim_decision(request_id: int, *, decision: Any, decided_by: str, note: Any,
                   row_filters: Tuple = ()) -> Tuple[int, Any]:
    """
    Decide a pending request in the inbox: (200, snapshot), or an HTTP status and a message.

    - 404 when no row has that id among row_filters (another workspace's), whatever
      the decision says;
    - 400 for a decision other than "approve" or "reject", or a note that isn't text;
    - 409 when the request is answered in the chat, or no longer pending: the
      update only takes a pending row, so of two clicks one wins.

    On 200 the row is decided and audited, and resume_status is "resuming"; the
    caller resumes the run (approval_resume.resume_in_background).
    """
    session = _session()
    if not session:
        return 503, "Database unavailable"
    try:
        row = session.query(ApprovalRequest).filter(
            ApprovalRequest.id == request_id, *row_filters).first()
        if not row:
            return 404, "Approval request not found"
        if decision not in DECISIONS:
            return 400, 'decision must be "approve" or "reject"'
        if note is not None and not isinstance(note, str):
            return 400, "note must be text"
        if not inbox_decides(row.source, row.approver):
            return 409, "This request is answered in the chat it came from."
        status = APPROVED if DECISIONS[decision] else REJECTED
        note = (note or "").strip()[:2000] or None
        result = session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == request_id, ApprovalRequest.status == PENDING)
            .values(status=status, decided_by=decided_by, decided_via="inbox",
                    decided_at=_now(), note=note, resume_status=RESUMING))
        session.commit()
        if result.rowcount != 1:
            return 409, "This request has already been decided."
        session.refresh(row)
        _audit(row, status, "inbox", note=note)
        return 200, {**snapshot(row), "confirmed": DECISIONS[decision]}
    except Exception as e:
        logger.error("Approval decision failed: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return 500, "The decision could not be saved."
    finally:
        session.close()


def expire_due(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Expire the inbox's pending requests whose time is up; returns their snapshots.

    Each becomes "expired" with an approval.expired audit entry by "system", and is
    left "resuming": the caller resumes its run with a rejection. A request only a
    chat answers (inbox_decides is false) doesn't expire: the person chatting may
    still answer it.
    """
    session = _session()
    if not session:
        return []
    expired: List[Dict[str, Any]] = []
    try:
        cutoff = now or _now()
        due = (session.query(ApprovalRequest.id)
               .filter(ApprovalRequest.status == PENDING, inbox_clause(),
                       ApprovalRequest.expires_at.isnot(None), ApprovalRequest.expires_at <= cutoff)
               .all())
        for (request_id,) in due:
            result = session.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == request_id, ApprovalRequest.status == PENDING)
                .values(status=EXPIRED, decided_by="system", decided_via="system",
                        decided_at=_now(), resume_status=RESUMING))
            session.commit()
            if result.rowcount != 1:
                continue
            row = session.get(ApprovalRequest, request_id)
            _audit(row, EXPIRED, "system")
            expired.append(snapshot(row))
        return expired
    except Exception as e:
        logger.warning("Approval expiry skipped: %s", e)
        try:
            session.rollback()
        except Exception:
            pass
        return expired
    finally:
        session.close()


def cancel_in_transaction(session: Any, agent_names: Iterable[str],
                          actor: Optional[str] = None) -> List[int]:
    """
    Cancel the pending requests of agents being deleted, in the caller's transaction.

    A request goes with the agent that asked or the root of its run. Nothing is
    resumed: the run has nowhere to go. The approval.cancelled entries are written
    once the caller commits, so a rolled-back deletion leaves none. Returns the ids.
    """
    from sqlalchemy import event, or_
    names = sorted({n for n in agent_names if n})
    if not names:
        return []
    ids = [i for (i,) in session.query(ApprovalRequest.id).filter(
        ApprovalRequest.status == PENDING,
        or_(ApprovalRequest.agent_name.in_(names), ApprovalRequest.app_name.in_(names))).all()]
    if not ids:
        return []
    session.query(ApprovalRequest).filter(
        ApprovalRequest.id.in_(ids), ApprovalRequest.status == PENDING,
    ).update({ApprovalRequest.status: CANCELLED, ApprovalRequest.decided_by: actor or "system",
              ApprovalRequest.decided_via: "system", ApprovalRequest.decided_at: _now()},
             synchronize_session=False)

    def audit_after_commit(_committed: Any) -> None:
        audit_session = _session()
        if not audit_session:
            return
        try:
            for row in audit_session.query(ApprovalRequest).filter(ApprovalRequest.id.in_(ids)):
                _audit(row, CANCELLED, "system", reason="agent deleted")
        except Exception as e:
            logger.warning("approval.cancelled entries not written: %s", e)
        finally:
            audit_session.close()

    event.listen(session, "after_commit", audit_after_commit, once=True)
    return ids


def pending_in_session(session_id: str) -> List[int]:
    """Ids of the requests still pending in a session."""
    session = _session()
    if not session or not session_id:
        return []
    try:
        return [i for (i,) in session.query(ApprovalRequest.id).filter(
            ApprovalRequest.session_id == session_id, ApprovalRequest.status == PENDING).all()]
    except Exception as e:
        logger.warning("Pending approval lookup failed: %s", e)
        return []
    finally:
        session.close()


def waiting_in_session(session_id: str) -> List[int]:
    """Ids of a session's requests still pending, or decided and still being resumed."""
    session = _session()
    if not session or not session_id:
        return []
    try:
        return [i for (i,) in session.query(ApprovalRequest.id).filter(
            ApprovalRequest.session_id == session_id,
            or_(ApprovalRequest.status == PENDING, ApprovalRequest.resume_status == RESUMING)).all()]
    except Exception as e:
        logger.warning("Waiting approval lookup failed: %s", e)
        return []
    finally:
        session.close()


def finish_resume(request_id: int, status: str, result: Optional[str]) -> None:
    """Store what happened when the run was resumed."""
    session = _session()
    if not session:
        return
    try:
        session.execute(update(ApprovalRequest).where(ApprovalRequest.id == request_id).values(
            resume_status=status, resume_result=(result or "")[:MAX_RESULT_CHARS] or None))
        session.commit()
    except Exception as e:
        logger.warning("Resume outcome not stored for request %s: %s", request_id, e)
        try:
            session.rollback()
        except Exception:
            pass
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
