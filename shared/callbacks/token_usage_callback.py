"""
Token usage callback for tracking LLM token consumption.

This callback can be used with any agent to capture and log token usage information.
Now uses database for persistent storage instead of session state.
"""

from contextvars import ContextVar
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from typing import Optional, Dict, Any
import logging
import uuid
from datetime import datetime
from ..utils.token_usage_service import get_token_usage_service

logger = logging.getLogger(__name__)

# ContextVar for current inference span - do NOT store span in callback_context.state
# (ADK persists state and Pydantic cannot serialize OpenTelemetry Span)
_inference_span_var: ContextVar[Optional[Any]] = ContextVar("_inference_span", default=None)

# Fallback: map callback_context id -> span so "after" callback can end span when it
# runs in a different async context (contextvar would be None).
_inference_span_by_context_id: Dict[int, Any] = {}

def _get_provider_from_model(model_name: Optional[str]) -> str:
    """Infer GenAI provider from model name."""
    if not model_name:
        return "unknown"
    m = (model_name or "").lower()
    if "gemini" in m or "models/" in m:
        return "gcp.gen_ai"
    if "openai" in m or "gpt" in m:
        return "openai"
    if "openrouter" in m:
        return "openrouter"
    return "unknown"


def _set_config_identity(span: Any, agent_name: str) -> None:
    """The config version the agent was built from, and its workspace, when recorded."""
    from shared.utils.agent_config_identity import loaded
    from shared.utils.tracing.genai_attributes import (
        MATE_AGENT_CONFIG_HASH, MATE_AGENT_CONFIG_VERSION, MATE_AGENT_CONFIG_VERSION_ID,
        MATE_WORKSPACE_ID)
    identity = loaded(agent_name)
    if not identity:
        return
    span.set_attribute(MATE_AGENT_CONFIG_HASH, identity["hash"])
    if identity.get("workspace_id") is not None:
        span.set_attribute(MATE_WORKSPACE_ID, identity["workspace_id"])
    if identity.get("version_id") is not None:
        span.set_attribute(MATE_AGENT_CONFIG_VERSION_ID, identity["version_id"])
        span.set_attribute(MATE_AGENT_CONFIG_VERSION, identity["version"])


def start_inference_span(callback_context: CallbackContext, model_name: Optional[str],
                         start_time: Optional[int] = None) -> None:
    """Start the gen_ai.inference span of a model call, when tracing is enabled.
    log_token_usage_callback ends it, or the error callback on a failed call.

    start_time (ns since the epoch) is when the call began, for one already answered.
    """
    try:
        from shared.utils.tracing.tracing_config import is_tracing_enabled
        if is_tracing_enabled():
            from opentelemetry import trace
            from shared.utils.tracing.tracer import get_tracer
            from shared.utils.tracing.genai_attributes import (
                GEN_AI_OPERATION_NAME,
                GEN_AI_PROVIDER_NAME,
                GEN_AI_REQUEST_MODEL,
                GEN_AI_CONVERSATION_ID,
                MATE_AGENT_NAME,
            )
            tracer = get_tracer("mate", "1.0.0")
            span = tracer.start_span("gen_ai.inference", start_time=start_time)
            logger.debug(
                "Tracing: gen_ai.inference span started agent=%s",
                getattr(callback_context, "agent_name", None),
            )
            span.set_attribute(GEN_AI_OPERATION_NAME, "chat")
            span.set_attribute(GEN_AI_PROVIDER_NAME, _get_provider_from_model(model_name))
            if model_name:
                span.set_attribute(GEN_AI_REQUEST_MODEL, model_name)
            agent_name = getattr(callback_context, 'agent_name', None)
            if agent_name:
                span.set_attribute(MATE_AGENT_NAME, agent_name)
                _set_config_identity(span, agent_name)
            user_id, session_id = _get_adk_session_info(callback_context)
            if session_id:
                span.set_attribute(GEN_AI_CONVERSATION_ID, session_id)
            # Store in contextvar so log_token_usage_callback can end it (same async context)
            _inference_span_var.set(span)
            # Fallback: key by context id so we can end span even if after-callback runs in different context
            ctx_id = id(callback_context)
            old = _inference_span_by_context_id.pop(ctx_id, None)
            if old is not None:
                try:
                    old.end()
                except Exception:
                    pass
            _inference_span_by_context_id[ctx_id] = span
            # Set as current span so RBAC/tool spans become children of this trace
            trace.set_span_in_context(span)
    except Exception as e:
        logger.debug("Tracing span start skipped: %s", e)


def capture_model_name_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Captures the model name from the request and stores it in session state.
    This should be called before the model call.
    Starts gen_ai.inference span when tracing is enabled.
    
    Args:
        callback_context: The callback context containing session state
        llm_request: The LLM request containing model information
        
    Returns:
        None to continue normal processing
    """
    # Get model name from request
    model_name = None
    
    # Try different ways to get model name from request
    if hasattr(llm_request, 'model') and llm_request.model:
        model_name = str(llm_request.model)
    elif hasattr(llm_request, 'model_name') and llm_request.model_name:
        model_name = llm_request.model_name
    
    # Store in session state for the token usage callback to use
    if not hasattr(callback_context, 'state'):
        callback_context.state = {}
    
    # Set by the fallback callback when the fallback model answers this call
    callback_context.state['current_model_is_fallback'] = False
    if model_name:
        callback_context.state['current_model_name'] = model_name
        logger.debug(f"Captured model name: {model_name}")
    else:
        logger.warning("Could not capture model name from request")
    
    start_inference_span(callback_context, model_name)
    
    return None  # Continue with normal processing


def log_token_usage_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """
    Callback that logs token usage information from LLM responses.
    Now saves data to Supabase database instead of session state.
    
    Args:
        callback_context: The callback context containing session state
        llm_response: The LLM response containing usage metadata
        
    Returns:
        None to continue normal processing, or LlmResponse to override
    """
    if llm_response.usage_metadata:
        usage = llm_response.usage_metadata
        
        # The invocation id groups every model call made while answering one user
        # turn, so "what did this response cost" is a GROUP BY. A per-call uuid, which
        # this used to be, cannot be tied back to a response at all. The LangGraph
        # runtime already keys its rows this way.
        request_id = _get_invocation_id(callback_context) or str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        # Get session information from ADK context using the proper properties
        user_id, session_id = _get_adk_session_info(callback_context)
        
        # Get model name from session state (captured by capture_model_name_callback)
        model_name = None
        is_fallback = False
        if hasattr(callback_context, 'state') and callback_context.state:
            model_name = callback_context.state.get('current_model_name')
            is_fallback = bool(callback_context.state.get('current_model_is_fallback'))
        
        # Fallback: try to get model name from response if not captured
        if not model_name:
            model_name = getattr(llm_response, 'model_name', None)
            if not model_name and hasattr(llm_response, 'model'):
                model_name = str(llm_response.model)
        
        # Final fallback: try to get from usage metadata
        if not model_name and usage:
            model_name = getattr(usage, 'model_name', None)
        
        # Log token usage with request ID
        logger.info(f"Token usage for agent '{callback_context.agent_name}' (Request: {request_id}):")
        logger.info(f"  User ID: {user_id}")
        logger.info(f"  Session ID: {session_id}")
        logger.info(f"  Prompt tokens: {usage.prompt_token_count}")
        logger.info(f"  Response tokens: {usage.candidates_token_count}")
        logger.info(f"  Total tokens: {usage.total_token_count}")
        
        # Prepare data for Supabase
        token_data = {
            "user_id": user_id,
            "timestamp": timestamp,
            "agent_name": callback_context.agent_name,
            "model_name": model_name,
            "request_id": request_id,
            "session_id": session_id,
            "prompt_tokens": usage.prompt_token_count or 0,
            "response_tokens": usage.candidates_token_count or 0,
            "thoughts_tokens": usage.thoughts_token_count or 0,
            "tool_use_tokens": usage.tool_use_prompt_token_count or 0,
            "is_fallback": is_fallback,
        }
        
        # Save to database
        token_usage_service = get_token_usage_service()
        if token_usage_service.is_connected():
            result = token_usage_service.insert_token_usage(token_data)
            if result:
                logger.info(f"Successfully saved token usage to database: {result.get('id')}")
                # Budget alerts used to be checked here, costing a DB query and a
                # blocking HTTP POST on every response. They are evaluated on the
                # scheduler now — see shared/utils/alert_service.py.
            else:
                logger.error("Failed to save token usage to database")
        else:
            logger.error("Database client not connected, cannot save token usage")
        
        # Keep backward compatibility with session state for existing code
        if not hasattr(callback_context, 'state'):
            callback_context.state = {}
        
        # Store current call details with enhanced metadata for backward compatibility
        current_call = {
            "request_id": request_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "agent_name": callback_context.agent_name,
            "prompt_tokens": usage.prompt_token_count,
            "response_tokens": usage.candidates_token_count,
            "total_tokens": usage.total_token_count,
            "cached_tokens": usage.cached_content_token_count,
            "thoughts_tokens": usage.thoughts_token_count,
            "tool_use_tokens": usage.tool_use_prompt_token_count,
            "model_name": model_name,
            "user_id": user_id
        }
        
        # Note: Data is now stored in Supabase database above, no need for global registry
        
        # Also store in local session state for backward compatibility
        if "all_token_usage" not in callback_context.state:
            callback_context.state["all_token_usage"] = []
        
        callback_context.state["all_token_usage"].append(current_call)
        
        # Also accumulate total usage across all calls
        if "total_token_usage" not in callback_context.state:
            callback_context.state["total_token_usage"] = {
                "total_prompt_tokens": 0,
                "total_response_tokens": 0,
                "total_tokens": 0,
                "call_count": 0
            }
        
        total_usage = callback_context.state["total_token_usage"]
        total_usage["total_prompt_tokens"] += usage.prompt_token_count or 0
        total_usage["total_response_tokens"] += usage.candidates_token_count or 0
        total_usage["total_tokens"] += usage.total_token_count or 0
        total_usage["call_count"] += 1
        
        logger.info(f"Session total tokens: {total_usage['total_tokens']} "
                   f"({total_usage['call_count']} calls)")
        
        # End GenAI inference span and set usage attributes
        try:
            from shared.utils.tracing.genai_attributes import (
                GEN_AI_USAGE_INPUT_TOKENS,
                GEN_AI_USAGE_OUTPUT_TOKENS,
                GEN_AI_RESPONSE_MODEL,
                MATE_MODEL_FALLBACK,
            )
            span = _inference_span_var.get()
            from_contextvar = span is not None
            if span is None:
                span = _inference_span_by_context_id.pop(id(callback_context), None)
            if span:
                span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, usage.prompt_token_count or 0)
                span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, usage.candidates_token_count or 0)
                if model_name:
                    span.set_attribute(GEN_AI_RESPONSE_MODEL, model_name)
                if is_fallback:
                    span.set_attribute(MATE_MODEL_FALLBACK, True)
                span.end()
                logger.debug("trace: gen_ai.inference span ended (from_contextvar=%s)", from_contextvar)
                _inference_span_var.set(None)
                _inference_span_by_context_id.pop(id(callback_context), None)
        except Exception as e:
            logger.debug("Tracing span end skipped: %s", e)
    
    return None  # Continue with normal processing


def _get_invocation_id(callback_context: CallbackContext) -> Optional[str]:
    """Return the ADK invocation id for the turn being answered, if reachable."""
    try:
        invocation = getattr(callback_context, '_invocation_context', None)
        return getattr(invocation, 'invocation_id', None) if invocation else None
    except Exception:
        return None


def _get_adk_session_info(callback_context: CallbackContext) -> tuple[str, str]:
    """
    Get both user_id and session_id from ADK callback context using the proper invocation context.
    
    Args:
        callback_context: The callback context
        
    Returns:
        tuple[str, str]: (user_id, session_id)
    """
    user_id = None
    session_id = None
    
    # Try to get values from invocation context first (proper ADK way)
    if hasattr(callback_context, '_invocation_context') and callback_context._invocation_context:
        # Get user_id from invocation context
        user_id = getattr(callback_context._invocation_context, 'user_id', None)
        
        # Get session_id from invocation context session
        if hasattr(callback_context._invocation_context, 'session') and callback_context._invocation_context.session:
            session_id = getattr(callback_context._invocation_context.session, 'id', None)
    
    # Fallback to direct context attributes if invocation_context is not accessible
    if not session_id:
        session_id = getattr(callback_context, 'session_id', None)
    
    if not user_id:
        if hasattr(callback_context, 'session') and callback_context.session:
            user_id = getattr(callback_context.session, 'user_id', None)
        if not user_id:
            user_id = getattr(callback_context, 'user_id', None)
    
    # Final fallbacks
    if not session_id:
        session_id = str(uuid.uuid4())
        logger.warning(f"No session_id found, generated: {session_id}")
    
    if not user_id:
        user_id = session_id
        logger.warning(f"No user_id found, using session_id: {session_id}")
    
    return user_id, session_id


# New Supabase-specific functions


