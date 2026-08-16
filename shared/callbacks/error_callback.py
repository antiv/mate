"""
Agent error recording: writes a `token_usage_logs` row when a model call fails.

Without this the failure path is silent. `token_usage_logs.status` has always
declared an ERROR value, but nothing wrote it: ADK never runs the after-model
callback when the model raises, and that callback is gated on usage metadata a
failed response does not carry. Both runtimes funnel through here so the row has
one writer and one shape.

Only *model* errors reach this module. Tool failures and errors outside the model
call remain unrecorded.
"""

import logging
import uuid
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest

from ..utils.token_usage_service import get_token_usage_service
from .token_usage_callback import (
    _get_adk_session_info,
    _inference_span_by_context_id,
    _inference_span_var,
)

logger = logging.getLogger(__name__)

# token_usage_logs.error_description is Text, but the string comes from an
# arbitrary provider exception — cap it so one bad traceback cannot bloat the table.
MAX_ERROR_DESCRIPTION = 2000


def record_agent_error(agent_name: Optional[str], user_id: Optional[str],
                       session_id: Optional[str], error: BaseException,
                       request_id: Optional[str] = None,
                       model_name: Optional[str] = None) -> bool:
    """Write one ERROR row for a failed agent call. Never raises.

    Shared by both runtimes. Callers are already on a failure path, so a logging
    problem here must never replace or mask the original error.
    """
    try:
        description = f"{type(error).__name__}: {error}"[:MAX_ERROR_DESCRIPTION]
        get_token_usage_service().log_token_usage(
            request_id=request_id or str(uuid.uuid4()),
            session_id=session_id,
            user_id=user_id,
            agent_name=agent_name,
            model_name=model_name,
            prompt_tokens=0,
            response_tokens=0,
            thoughts_tokens=0,
            tool_use_tokens=0,
            status='ERROR',
            error_description=description,
        )
        logger.info(f"Recorded agent error for {agent_name}: {description}")
        return True
    except Exception as e:
        logger.warning(f"Failed to record agent error for {agent_name}: {e}")
        return False


def _end_inference_span(callback_context: CallbackContext) -> None:
    """End the GenAI span opened by capture_model_name_callback.

    Only the after-model callback ends it, and that never runs on a failed call,
    so without this every model error leaks a span.
    """
    try:
        span = _inference_span_var.get()
        if span is None:
            span = _inference_span_by_context_id.pop(id(callback_context), None)
        if span:
            span.end()
            _inference_span_var.set(None)
            _inference_span_by_context_id.pop(id(callback_context), None)
    except Exception as e:
        logger.debug("Tracing span end on error skipped: %s", e)


def record_model_error_callback(*, callback_context: CallbackContext,
                                llm_request: LlmRequest,
                                error: Exception) -> None:
    """ADK `on_model_error_callback`: record the failure, then let it propagate.

    Returning None is what makes this a pure observer — ADK re-raises the original
    error. Returning an LlmResponse would swallow it.
    """
    try:
        agent_name = getattr(callback_context, 'agent_name', None)
        user_id, session_id = _get_adk_session_info(callback_context)
        model_name = None
        try:
            model_name = callback_context.state.get('current_model_name')
        except Exception:
            pass
        record_agent_error(
            agent_name=agent_name,
            user_id=user_id,
            session_id=session_id,
            error=error,
            model_name=model_name,
        )
        _end_inference_span(callback_context)
    except Exception as e:
        logger.warning(f"record_model_error_callback failed: {e}")
    return None
