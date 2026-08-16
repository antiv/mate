"""
Run-loop orchestration for the LangGraph runtime: wires hooks (RBAC, guardrails),
graph invocation and the ADK-shape event translation behind POST /run_sse.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict

logger = logging.getLogger(__name__)


def _sse_frame(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _error_event(author: str, text: str, invocation_id: str) -> Dict[str, Any]:
    """A complete ADK-shaped event carrying an error/notice message."""
    from datetime import datetime, timezone
    return {
        "id": str(uuid.uuid4()),
        "author": author,
        "invocationId": invocation_id,
        "content": {"role": "model", "parts": [{"text": text}]},
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }


async def run_sse_stream(app_name: str, user_id: str, session_id: str,
                         new_message: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """Stream ADK Event JSON frames for one /run_sse invocation."""
    invocation_id = f"e-{uuid.uuid4()}"
    # This is the LangGraph equivalent of the ADK run boundary: every surface on this
    # runtime arrives through /run_sse, and it has no A2A or MCP of its own.
    started_monotonic = time.monotonic()
    status = "SUCCESS"
    try:
        from shared.utils.response_metrics import start_response
        start_response(invocation_id=invocation_id, started_at=datetime.now(timezone.utc),
                       session_id=session_id, user_id=user_id, agent_name=app_name)
    except Exception:
        logger.debug("[LangGraph] opening response metrics failed", exc_info=True)
    try:
        from shared.utils.langgraph.executor import execute_run
        async for event in execute_run(app_name=app_name, user_id=user_id,
                                       session_id=session_id, new_message=new_message,
                                       invocation_id=invocation_id):
            yield _sse_frame(event)
    except Exception as e:
        status = "ERROR"
        logger.exception(f"[LangGraph] run_sse failed for app={app_name} session={session_id}")
        # Own try/except: a logging failure must not stop the error frame reaching the client
        try:
            from shared.callbacks.error_callback import record_agent_error
            record_agent_error(agent_name=app_name, user_id=user_id, session_id=session_id,
                               error=e, request_id=invocation_id)
        except Exception:
            logger.debug("[LangGraph] recording the agent error failed", exc_info=True)
        yield _sse_frame({
            "error_code": "INTERNAL_ERROR",
            "error_message": str(e),
            **_error_event(app_name, "An error occurred while processing your request.", invocation_id),
        })
    finally:
        try:
            from shared.utils.response_metrics import finish_response
            finish_response(
                invocation_id=invocation_id,
                duration_ms=int((time.monotonic() - started_monotonic) * 1000),
                status=status,
            )
        except Exception:
            logger.debug("[LangGraph] closing response metrics failed", exc_info=True)
