"""
Run-loop orchestration for the LangGraph runtime: wires hooks (RBAC, guardrails),
graph invocation and the ADK-shape event translation behind POST /run_sse.
"""

import json
import logging
import uuid
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
    try:
        from shared.utils.langgraph.executor import execute_run
        async for event in execute_run(app_name=app_name, user_id=user_id,
                                       session_id=session_id, new_message=new_message,
                                       invocation_id=invocation_id):
            yield _sse_frame(event)
    except Exception as e:
        logger.exception(f"[LangGraph] run_sse failed for app={app_name} session={session_id}")
        yield _sse_frame({
            "error_code": "INTERNAL_ERROR",
            "error_message": str(e),
            **_error_event(app_name, "An error occurred while processing your request.", invocation_id),
        })
