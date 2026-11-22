"""
Approval capture for the ADK runtime.

Registered on every App, like MetricsPlugin, because it is the one place that sees
every surface: A2A and MCP calls never reach the auth server. It only records
(shared/utils/approval_service.py) and never changes an event or a message:

- on_event_callback: each adk_request_confirmation call an agent emits becomes a
  pending row;
- on_user_message_callback: each answer to one marks its row approved or rejected,
  with an audit entry.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from ..utils import approval_service

logger = logging.getLogger(__name__)


def _decisions(content: Optional[types.Content]) -> Dict[str, bool]:
    """The approve/reject answers in a user message, by confirmation call id."""
    decisions: Dict[str, bool] = {}
    for part in (content.parts if content and content.parts else ()):
        response = part.function_response
        if (response and response.name == approval_service.CONFIRMATION_CALL_NAME
                and response.id):
            payload: Any = response.response or {}
            decisions[response.id] = bool(payload.get("confirmed")) if isinstance(payload, dict) else False
    return decisions


class ApprovalCapturePlugin(BasePlugin):
    """Records approval requests and the chat's answers to them."""

    def __init__(self, name: str = "mate_approvals"):
        super().__init__(name)

    async def on_user_message_callback(
        self, *, invocation_context: InvocationContext, user_message: types.Content
    ) -> Optional[types.Content]:
        try:
            decisions = _decisions(user_message)
            if decisions:
                session_id = invocation_context.session.id
                user_id = invocation_context.user_id
                for call_id, confirmed in decisions.items():
                    await asyncio.to_thread(
                        approval_service.record_decision, session_id=session_id,
                        confirmation_call_id=call_id, confirmed=confirmed,
                        decided_by=user_id, via="chat")
        except Exception as e:
            logger.debug("Approval decision capture skipped: %s", e)
        return None

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
                    await asyncio.to_thread(
                        approval_service.record_request,
                        confirmation_call_id=call.id, args=call.args,
                        agent_name=event.author, app_name=invocation_context.app_name,
                        user_id=invocation_context.user_id, session_id=session.id,
                        invocation_id=invocation_context.invocation_id,
                        trigger_id=trigger_id, source=surface)
        except Exception as e:
            logger.debug("Approval request capture skipped: %s", e)
        return None
