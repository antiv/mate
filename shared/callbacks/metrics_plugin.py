"""
Response timing plugin for the ADK runtime.

Registered unconditionally, unlike MatePlugin — every surface that reaches the ADK
Runner is timed, including A2A and MCP calls that never touch the auth server. It
implements only the run-level hooks, so it cannot double-execute against the
per-agent model callbacks wired in agent_manager.
"""

import logging
import time
from typing import Any, Dict, Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from ..utils.response_metrics import finish_response, start_response

logger = logging.getLogger(__name__)


class MetricsPlugin(BasePlugin):
    """Times each invocation and writes one agent_responses row."""

    def __init__(self, name: str = "mate_metrics"):
        super().__init__(name)
        # invocation_id -> (monotonic start, wall-clock start)
        self._started: Dict[str, tuple] = {}

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        try:
            from datetime import datetime, timezone
            invocation_id = invocation_context.invocation_id
            self._started[invocation_id] = time.monotonic()
            session = getattr(invocation_context, 'session', None)
            agent = getattr(invocation_context, 'agent', None)
            start_response(
                invocation_id=invocation_id,
                started_at=datetime.now(timezone.utc),
                session_id=getattr(session, 'id', None) if session else None,
                user_id=getattr(invocation_context, 'user_id', None),
                agent_name=getattr(agent, 'name', None) if agent else None,
            )
        except Exception as e:
            logger.debug("Response timing start skipped: %s", e)
        return None

    async def after_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> None:
        try:
            invocation_id = invocation_context.invocation_id
            monotonic_start = self._started.pop(invocation_id, None)
            if monotonic_start is None:
                return
            finish_response(
                invocation_id=invocation_id,
                duration_ms=int((time.monotonic() - monotonic_start) * 1000),
            )
        except Exception as e:
            logger.debug("Response timing skipped: %s", e)
        return None
