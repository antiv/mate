"""
Approvals for the ADK runtime.

Registered on every App, like MetricsPlugin, because it is the one place that sees
every surface: A2A and MCP calls never reach the auth server
(shared/utils/approval_service.py):

- on_event_callback: each adk_request_confirmation call an agent emits becomes a
  pending row. When the chat may not answer it, the call's args get
  "mateApprover": "inbox" ("either" when both may), which the chat clients read
  to show no buttons.
- on_user_message_callback: each answer to one passes approval_service.gate_answer.
  An answer the chat may give decides its row; the inbox's resume carries its
  decision; any other answer (to a request the inbox answers, or a second one) is
  taken out of the message, and a message left empty says so in text. Every
  answer that goes on is rewritten as a plain {"confirmed": ...}, so ADK reads
  exactly what the gate allowed; when the check fails, no answer goes on.
"""

import asyncio
import logging
from typing import Dict, Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from ..utils import approval_service

logger = logging.getLogger(__name__)

APPROVER_ARG = "mateApprover"
DROPPED_TEXT = ("(An answer to an approval request was not passed on: the request is decided "
                "in the approvals inbox, or was answered already.)")


def _is_answer(part: types.Part) -> bool:
    response = part.function_response
    return bool(response and response.name == approval_service.CONFIRMATION_CALL_NAME)


def _decisions(content: Optional[types.Content]) -> Dict[str, bool]:
    """The approve/reject answers in a user message, by confirmation call id, read as ADK reads them."""
    decisions: Dict[str, bool] = {}
    for part in (content.parts if content and content.parts else ()):
        if _is_answer(part) and part.function_response.id:
            decisions[part.function_response.id] = approval_service.answer_confirmed(
                part.function_response.response or {})
    return decisions


def _rewritten(message: types.Content, allowed: Dict[str, Optional[bool]]) -> types.Content:
    """The message with each confirmation answer as allowed: its decision, or taken out."""
    parts = []
    for part in message.parts or ():
        if not _is_answer(part):
            parts.append(part)
            continue
        response = part.function_response
        confirmed = allowed.get(response.id)
        if confirmed is None:
            continue
        # Only what the gate allowed: no wrapper or second field for ADK to read otherwise
        parts.append(types.Part(function_response=types.FunctionResponse(
            id=response.id, name=response.name, response={"confirmed": confirmed})))
    if not parts:
        parts = [types.Part(text=DROPPED_TEXT)]
    return types.Content(role=message.role or "user", parts=parts)


class ApprovalCapturePlugin(BasePlugin):
    """Records approval requests, and lets each one be answered once, by whoever may."""

    def __init__(self, name: str = "mate_approvals"):
        super().__init__(name)

    async def on_user_message_callback(
        self, *, invocation_context: InvocationContext, user_message: types.Content
    ) -> Optional[types.Content]:
        if not any(_is_answer(p) for p in (user_message.parts if user_message and user_message.parts else ())):
            return None
        allowed: Dict[str, Optional[bool]] = {}
        try:
            session_id = invocation_context.session.id
            user_id = invocation_context.user_id
            for call_id, confirmed in _decisions(user_message).items():
                allowed[call_id] = await asyncio.to_thread(
                    approval_service.gate_answer, session_id=session_id,
                    confirmation_call_id=call_id, confirmed=confirmed, answered_by=user_id)
        except Exception as e:
            # Fail closed: an answer that couldn't be checked doesn't reach the agent
            logger.warning("Approval answer check failed; the answers are dropped: %s", e)
            allowed = {}
        return _rewritten(user_message, allowed)

    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Optional[Event]:
        try:
            calls = [fc for fc in event.get_function_calls()
                     if fc.name == approval_service.CONFIRMATION_CALL_NAME]
            if calls:
                from ..utils import run_surface
                session = invocation_context.session
                trigger_id = (session.state or {}).get(approval_service.TRIGGER_STATE_KEY)
                surface = run_surface.current()
                for call in calls:
                    request_id = await asyncio.to_thread(
                        approval_service.record_request, confirmation_call_id=call.id, args=call.args,
                        agent_name=event.author, app_name=invocation_context.app_name,
                        user_id=invocation_context.user_id, session_id=session.id,
                        invocation_id=invocation_context.invocation_id,
                        trigger_id=trigger_id, source=surface)
                    answered_in = await asyncio.to_thread(approval_service.answering, request_id)
                    if answered_in != "chat" and isinstance(call.args, dict):
                        # In place: the runner appends and streams this same event
                        call.args[APPROVER_ARG] = answered_in
        except Exception as e:
            logger.debug("Approval request capture skipped: %s", e)
        return None
