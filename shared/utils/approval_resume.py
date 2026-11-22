"""
Resuming a run after the inbox decided its approval request.

The auth server sends the answer to the agent server on the session the request
was recorded in, the same way the trigger runner sends a prompt: a function
response named adk_request_confirmation, with the request's confirmation call id
and {confirmed}. Both runtimes take it as the chat's answer would be taken
(documents/design/approvals-inbox.md, "Resuming the run").

Resuming runs in a thread, so a slow agent doesn't hold up the click; the row says
"resuming" until it finishes. Then:

- a trigger's run, when nothing in its session is pending any more, has its reply
  routed through the trigger's output (TriggerRunner.deliver_after_approval);
- any other run's reply stays in its session.
"""

import logging
import threading
from typing import Any, Dict, List

from . import approval_service

logger = logging.getLogger(__name__)


def answer_message(confirmation_call_id: str, confirmed: bool) -> Dict[str, Any]:
    """The new_message that answers a confirmation call."""
    return {"role": "user", "parts": [{"function_response": {
        "id": confirmation_call_id,
        "name": approval_service.CONFIRMATION_CALL_NAME,
        "response": {"confirmed": bool(confirmed)},
    }}]}


def resume(request: Dict[str, Any], confirmed: bool) -> Dict[str, Any]:
    """
    Send the decision to the paused run and wait for it to finish; returns
    {"status", "reply"}. Never raises: a failure is stored on the row.
    """
    from .trigger_runner import collect_reply, get_trigger_runner
    from .utils import get_adk_config
    try:
        cfg = get_adk_config()
        reply = collect_reply(cfg["adk_host"], cfg["adk_port"], {
            "app_name": request["app_name"],
            "user_id": request["user_id"],
            "session_id": request["session_id"],
            "new_message": answer_message(request["confirmation_call_id"], confirmed),
            "streaming": True,
        }, timeout=300.0)
    except Exception as e:
        logger.warning("Resuming the run of approval request %s failed: %s", request["id"], e)
        approval_service.finish_resume(request["id"], approval_service.RESUME_ERROR, str(e))
        return {"status": approval_service.RESUME_ERROR, "reply": None}

    approval_service.finish_resume(request["id"], approval_service.RESUME_OK, reply)
    if request.get("source") == "trigger" and request.get("trigger_id"):
        if not approval_service.pending_in_session(request["session_id"]):
            get_trigger_runner().deliver_after_approval(request["trigger_id"], reply)
    return {"status": approval_service.RESUME_OK, "reply": reply}


def resume_in_background(request: Dict[str, Any], confirmed: bool) -> threading.Thread:
    """resume() in a daemon thread."""
    thread = threading.Thread(target=resume, args=(request, confirmed), daemon=True,
                              name=f"approval-resume-{request['id']}")
    thread.start()
    return thread


def expire_and_resume() -> List[int]:
    """The periodic job: expire what is due and resume each run with a rejection."""
    expired = approval_service.expire_due()
    for request in expired:
        resume(request, confirmed=False)
    return [r["id"] for r in expired]
