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

# Track which alerts we've already sent this period to avoid spam
_budget_alert_sent: Dict[str, set] = {}  # key -> set of threshold_pct


def _maybe_fire_budget_alerts(user_id: Optional[str], agent_name: Optional[str], usage: Any):
    """Check rate limit configs and fire webhook when crossing alert thresholds."""
    if not user_id and not agent_name:
        return
    try:
        from datetime import timedelta
        from ..utils.rate_limit_service import get_rate_limit_service
        from ..utils.database_client import get_database_client
        from ..utils.models import RateLimitConfig

        svc = get_rate_limit_service()
        db = get_database_client()
        if not db or not db.is_connected():
            return
        session = db.get_session()
        if not session:
            return
        try:
            now = datetime.now()
            token_service = get_token_usage_service()
            configs = []
            if user_id:
                uc = session.query(RateLimitConfig).filter(
                    RateLimitConfig.scope == "user",
                    RateLimitConfig.scope_id == user_id,
                ).first()
                if uc and uc.alert_webhook_url and uc.tokens_per_day:
                    tokens = token_service.get_user_tokens_since(
                        user_id, now - timedelta(days=1)
                    )
                    configs.append((uc, tokens, uc.tokens_per_day, "user", user_id))
            if agent_name:
                ac = session.query(RateLimitConfig).filter(
                    RateLimitConfig.scope == "agent",
                    RateLimitConfig.scope_id == agent_name,
                ).first()
                if ac and ac.alert_webhook_url and ac.tokens_per_day:
                    tokens = token_service.get_agent_tokens_since(
                        agent_name, now - timedelta(days=1)
                    )
                    configs.append((ac, tokens, ac.tokens_per_day, "agent", agent_name))
            for cfg, current, limit, scope, sid in configs:
                if limit is None or limit <= 0:
                    continue
                pct = int(100 * current / limit)
                for thresh in cfg.get_alert_thresholds():
                    if pct >= thresh:
                        key = f"{scope}:{sid}:day:{thresh}"
                        if key not in _budget_alert_sent:
                            _budget_alert_sent[key] = set()
                        if thresh not in _budget_alert_sent[key]:
                            svc.send_alert_webhook_sync(
                                scope, sid, thresh, current, limit, cfg.alert_webhook_url
                            )
                            _budget_alert_sent.setdefault(key, set()).add(thresh)
        finally:
            session.close()
    except Exception as e:
        logger.debug("Budget alert check failed: %s", e)


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
    
    if model_name:
        callback_context.state['current_model_name'] = model_name
        logger.debug(f"Captured model name: {model_name}")
    else:
        logger.warning("Could not capture model name from request")
    
    # Start GenAI inference span for distributed tracing
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
            span = tracer.start_span("gen_ai.inference")
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
        
        # Generate unique request ID for this LLM call
        request_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        # Get session information from ADK context using the proper properties
        user_id, session_id = _get_adk_session_info(callback_context)
        
        # Get model name from session state (captured by capture_model_name_callback)
        model_name = None
        if hasattr(callback_context, 'state') and callback_context.state:
            model_name = callback_context.state.get('current_model_name')
        
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
            "tool_use_tokens": usage.tool_use_prompt_token_count or 0
        }
        
        # Save to database
        token_usage_service = get_token_usage_service()
        if token_usage_service.is_connected():
            result = token_usage_service.insert_token_usage(token_data)
            if result:
                logger.info(f"Successfully saved token usage to database: {result.get('id')}")
                # Fire budget alerts if approaching thresholds (async, non-blocking)
                _maybe_fire_budget_alerts(user_id, callback_context.agent_name, usage)
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
                span.end()
                logger.debug("trace: gen_ai.inference span ended (from_contextvar=%s)", from_contextvar)
                _inference_span_var.set(None)
                _inference_span_by_context_id.pop(id(callback_context), None)
        except Exception as e:
            logger.debug("Tracing span end skipped: %s", e)
    
    return None  # Continue with normal processing


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


def _get_session_id(callback_context: CallbackContext) -> str:
    """
    Get session ID from ADK callback context using the proper invocation context.
    
    Args:
        callback_context: The callback context
        
    Returns:
        str: Session ID
    """
    # Try to get session ID from invocation context first (proper ADK way)
    if hasattr(callback_context, '_invocation_context') and callback_context._invocation_context:
        if hasattr(callback_context._invocation_context, 'session') and callback_context._invocation_context.session:
            session_id = getattr(callback_context._invocation_context.session, 'id', None)
            if session_id:
                return session_id
    
    # Fallback to direct context attributes
    session_id = getattr(callback_context, 'session_id', None)
    if not session_id:
        # Final fallback: generate a session ID if none provided
        session_id = str(uuid.uuid4())
        logger.warning(f"No session_id found in callback_context, generated: {session_id}")
    
    return session_id

def get_session_token_usage(callback_context: CallbackContext) -> dict:
    """
    Get token usage information from the current session.
    Queries from Supabase database.
    
    Args:
        callback_context: The callback context
        
    Returns:
        dict: Token usage information including all calls and totals
    """
    # Get session ID from ADK context
    session_id = _get_session_id(callback_context)
    
    # Get data from Supabase
    token_usage_service = get_token_usage_service()
    if token_usage_service.is_connected():
        try:
            # Get aggregated data from Supabase
            aggregated_data = token_usage_service.get_aggregated_token_usage(session_id)
            records = token_usage_service.get_token_usage_by_session(session_id)
            
            return {
                "all_calls": records,
                "total_usage": {
                    "total_prompt_tokens": aggregated_data.get("total_prompt_tokens", 0),
                    "total_response_tokens": aggregated_data.get("total_response_tokens", 0),
                    "total_tokens": aggregated_data.get("total_tokens", 0),
                    "call_count": aggregated_data.get("call_count", 0)
                },
                "session_id": session_id,
                "source": "supabase"
            }
        except Exception as e:
            logger.error(f"Failed to get data from Supabase: {e}")
            return {
                "all_calls": [],
                "total_usage": {
                    "total_prompt_tokens": 0,
                    "total_response_tokens": 0,
                    "total_tokens": 0,
                    "call_count": 0
                },
                "session_id": session_id,
                "source": "error"
            }
    else:
        logger.error("Supabase client not connected")
        return {
            "all_calls": [],
            "total_usage": {
                "total_prompt_tokens": 0,
                "total_response_tokens": 0,
                "total_tokens": 0,
                "call_count": 0
            },
            "session_id": session_id,
            "source": "disconnected"
        }

def get_agent_requests(callback_context: CallbackContext, agent_name: str = None) -> list:
    """
    Get all requests for a specific agent or all agents.
    
    Args:
        callback_context: The callback context
        agent_name: Specific agent name, or None for all agents
        
    Returns:
        list: List of request records
    """
    if not hasattr(callback_context, 'state'):
        return []
    
    all_calls = callback_context.state.get("all_token_usage", [])
    
    if agent_name:
        return [call for call in all_calls if call.get("agent_name") == agent_name]
    else:
        return all_calls

def get_request_by_id(callback_context: CallbackContext, request_id: str) -> dict:
    """
    Get a specific request by its ID.
    
    Args:
        callback_context: The callback context
        request_id: The request ID to find
        
    Returns:
        dict: Request record or empty dict if not found
    """
    all_calls = get_agent_requests(callback_context)
    for call in all_calls:
        if call.get("request_id") == request_id:
            return call
    return {}

def get_requests_by_timeframe(callback_context: CallbackContext, start_time: str = None, end_time: str = None) -> list:
    """
    Get requests within a specific timeframe.
    
    Args:
        callback_context: The callback context
        start_time: Start time in ISO format (optional)
        end_time: End time in ISO format (optional)
        
    Returns:
        list: Filtered list of request records
    """
    all_calls = get_agent_requests(callback_context)
    
    if not start_time and not end_time:
        return all_calls
    
    filtered_calls = []
    for call in all_calls:
        timestamp = call.get("timestamp", "")
        if start_time and timestamp < start_time:
            continue
        if end_time and timestamp > end_time:
            continue
        filtered_calls.append(call)
    
    return filtered_calls


def print_token_usage_summary(callback_context: CallbackContext):
    """
    Print a summary of token usage for the current session.
    
    Args:
        callback_context: The callback context
    """
    usage_info = get_session_token_usage(callback_context)
    
    print("\n" + "="*50)
    print("TOKEN USAGE SUMMARY")
    print("="*50)
    
    # Show session info
    session_id = usage_info.get("session_id")
    if session_id:
        print(f"Session ID: {session_id}")
    
    # Show all individual calls
    all_calls = usage_info.get("all_calls", [])
    if all_calls:
        print(f"All calls in session ({len(all_calls)} total):")
        for i, call in enumerate(all_calls, 1):
            print(f"  Call {i}: {call.get('agent_name', 'unknown')}")
            print(f"    Request ID: {call.get('request_id', 'N/A')}")
            print(f"    Timestamp: {call.get('timestamp', 'N/A')}")
            print(f"    Prompt tokens: {call.get('prompt_tokens', 0)}")
            print(f"    Response tokens: {call.get('response_tokens', 0)}")
            print(f"    Total tokens: {call.get('total_tokens', 0)}")
            if call.get('cached_tokens', 0) > 0:
                print(f"    Cached tokens: {call.get('cached_tokens', 0)}")
            if call.get('thoughts_tokens', 0) > 0:
                print(f"    Thoughts tokens: {call.get('thoughts_tokens', 0)}")
            if call.get('tool_use_tokens', 0) > 0:
                print(f"    Tool use tokens: {call.get('tool_use_tokens', 0)}")
            if call.get('model_name'):
                print(f"    Model: {call.get('model_name')}")
            if call.get('user_query'):
                print(f"    Query: {call.get('user_query')[:100]}...")
            print()
    
    # Show session totals
    total = usage_info.get("total_usage", {})
    if total:
        print(f"Session totals:")
        print(f"  Total prompt tokens: {total.get('total_prompt_tokens', 0)}")
        print(f"  Total response tokens: {total.get('total_response_tokens', 0)}")
        print(f"  Total tokens: {total.get('total_tokens', 0)}")
        print(f"  Number of calls: {total.get('call_count', 0)}")
    
    print("="*50)


def get_agent_token_breakdown(callback_context: CallbackContext) -> dict:
    """
    Get token usage breakdown by agent for the current session.
    
    Args:
        callback_context: The callback context
        
    Returns:
        dict: Token usage breakdown by agent
    """
    usage_info = get_session_token_usage(callback_context)
    all_calls = usage_info.get("all_calls", [])
    
    agent_breakdown = {}
    
    for call in all_calls:
        agent_name = call.get('agent_name', 'unknown')
        if agent_name not in agent_breakdown:
            agent_breakdown[agent_name] = {
                "call_count": 0,
                "total_prompt_tokens": 0,
                "total_response_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "thoughts_tokens": 0,
                "tool_use_tokens": 0
            }
        
        breakdown = agent_breakdown[agent_name]
        breakdown["call_count"] += 1
        breakdown["total_prompt_tokens"] += call.get('prompt_tokens', 0) or 0
        breakdown["total_response_tokens"] += call.get('response_tokens', 0) or 0
        breakdown["total_tokens"] += call.get('total_tokens', 0) or 0
        breakdown["cached_tokens"] += call.get('cached_tokens', 0) or 0
        breakdown["thoughts_tokens"] += call.get('thoughts_tokens', 0) or 0
        breakdown["tool_use_tokens"] += call.get('tool_use_tokens', 0) or 0
    
    return agent_breakdown


def print_agent_breakdown(callback_context: CallbackContext):
    """
    Print token usage breakdown by agent.
    
    Args:
        callback_context: The callback context
    """
    breakdown = get_agent_token_breakdown(callback_context)
    
    if not breakdown:
        print("No token usage data available.")
        return
    
    print("\n" + "="*50)
    print("TOKEN USAGE BY AGENT")
    print("="*50)
    
    for agent_name, stats in breakdown.items():
        print(f"\n{agent_name}:")
        print(f"  Calls: {stats['call_count']}")
        print(f"  Total tokens: {stats['total_tokens']}")
        print(f"  Prompt tokens: {stats['total_prompt_tokens']}")
        print(f"  Response tokens: {stats['total_response_tokens']}")
        if stats['cached_tokens'] > 0:
            print(f"  Cached tokens: {stats['cached_tokens']}")
        if stats['thoughts_tokens'] > 0:
            print(f"  Thoughts tokens: {stats['thoughts_tokens']}")
        if stats['tool_use_tokens'] > 0:
            print(f"  Tool use tokens: {stats['tool_use_tokens']}")
    
    print("="*50)

def print_agent_requests(callback_context: CallbackContext, agent_name: str = None):
    """
    Print detailed request information for a specific agent or all agents.
    
    Args:
        callback_context: The callback context
        agent_name: Specific agent name, or None for all agents
    """
    if agent_name:
        requests = get_agent_requests(callback_context, agent_name)
        if not requests:
            print(f"No requests found for agent '{agent_name}'.")
            return
        
        print(f"\n{'='*60}")
        print(f"REQUESTS FOR AGENT: {agent_name}")
        print(f"{'='*60}")
        print(f"Total requests: {len(requests)}")
        
        for i, req in enumerate(requests, 1):
            print(f"\nRequest {i}:")
            print(f"  ID: {req.get('request_id', 'N/A')}")
            print(f"  Timestamp: {req.get('timestamp', 'N/A')}")
            print(f"  Session ID: {req.get('session_id', 'N/A')}")
            print(f"  Prompt tokens: {req.get('prompt_tokens', 0)}")
            print(f"  Response tokens: {req.get('response_tokens', 0)}")
            print(f"  Total tokens: {req.get('total_tokens', 0)}")
            if req.get('cached_tokens', 0) > 0:
                print(f"  Cached tokens: {req.get('cached_tokens', 0)}")
            if req.get('thoughts_tokens', 0) > 0:
                print(f"  Thoughts tokens: {req.get('thoughts_tokens', 0)}")
            if req.get('tool_use_tokens', 0) > 0:
                print(f"  Tool use tokens: {req.get('tool_use_tokens', 0)}")
            if req.get('model_name'):
                print(f"  Model: {req.get('model_name')}")
            if req.get('user_query'):
                print(f"  Query: {req.get('user_query')}")
    else:
        # Show all agents
        all_calls = get_agent_requests(callback_context)
        
        if not all_calls:
            print("No agent requests found.")
            return
        
        # Group by agent
        agent_requests = {}
        for call in all_calls:
            agent_name = call.get('agent_name', 'unknown')
            if agent_name not in agent_requests:
                agent_requests[agent_name] = []
            agent_requests[agent_name].append(call)
        
        print(f"\n{'='*60}")
        print("ALL AGENT REQUESTS")
        print(f"{'='*60}")
        
        for agent, requests in agent_requests.items():
            print(f"\n{agent} ({len(requests)} requests):")
            total_tokens = sum(req.get('total_tokens', 0) for req in requests)
            print(f"  Total tokens: {total_tokens}")
            
            for i, req in enumerate(requests, 1):
                print(f"    {i}. {req.get('timestamp', 'N/A')} - {req.get('total_tokens', 0)} tokens")
                if req.get('user_query'):
                    print(f"       Query: {req.get('user_query')[:80]}...")
    
    print(f"\n{'='*60}")

def export_session_to_json(callback_context: CallbackContext, filepath: str = None) -> str:
    """
    Export current session token usage to JSON file.
    
    Args:
        callback_context: The callback context
        filepath: Output file path (optional)
        
    Returns:
        str: Path to exported file or None if failed
    """
    import json
    
    if not filepath:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"session_token_usage_{timestamp}.json"
    
    try:
        usage_info = get_session_token_usage(callback_context)
        
        with open(filepath, 'w') as f:
            json.dump(usage_info, f, indent=2)
        
        logger.info(f"Exported session token usage to {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"Failed to export session token usage: {e}")
        return None


def get_clean_token_summary(callback_context: CallbackContext) -> dict:
    """
    Get a clean summary of token usage from Supabase database.
    
    Args:
        callback_context: The callback context
        
    Returns:
        dict: Clean token usage summary
    """
    # Get session ID from ADK context
    session_id = _get_session_id(callback_context)
    
    # Get data from Supabase
    token_usage_service = get_token_usage_service()
    if token_usage_service.is_connected():
        try:
            aggregated_data = token_usage_service.get_aggregated_token_usage(session_id)
            records = token_usage_service.get_token_usage_by_session(session_id)
            
            return {
                "session_id": session_id,
                "total_usage": {
                    "total_prompt_tokens": aggregated_data.get("total_prompt_tokens", 0),
                    "total_response_tokens": aggregated_data.get("total_response_tokens", 0),
                    "total_tokens": aggregated_data.get("total_tokens", 0),
                    "call_count": aggregated_data.get("call_count", 0)
                },
                "all_requests": records
            }
        except Exception as e:
            logger.error(f"Failed to get token summary from Supabase: {e}")
            return {
                "session_id": session_id,
                "total_usage": {
                    "total_prompt_tokens": 0,
                    "total_response_tokens": 0,
                    "total_tokens": 0,
                    "call_count": 0
                },
                "all_requests": []
            }
    else:
        logger.error("Supabase client not connected")
        return {
            "session_id": session_id,
            "total_usage": {
                "total_prompt_tokens": 0,
                "total_response_tokens": 0,
                "total_tokens": 0,
                "call_count": 0
            },
            "all_requests": []
        }

def get_last_request(callback_context: CallbackContext) -> dict:
    """
    Get the last (most recent) request from Supabase database.
    
    Args:
        callback_context: The callback context
        
    Returns:
        dict: Last request record or empty dict if no requests
    """
    # Get session ID from ADK context
    session_id = _get_session_id(callback_context)
    
    # Get data from Supabase
    token_usage_service = get_token_usage_service()
    if token_usage_service.is_connected():
        try:
            records = token_usage_service.get_token_usage_by_session(session_id)
            # Return the most recent record (last in the list)
            return records[-1] if records else {}
        except Exception as e:
            logger.error(f"Failed to get last request from Supabase: {e}")
            return {}
    else:
        logger.error("Supabase client not connected")
        return {}




def get_complete_token_data(session_id: str) -> dict:
    """
    Get complete token usage data for a session from Supabase.
    
    Args:
        session_id: The session ID to get token usage for
        
    Returns:
        dict: Complete token usage data
    """
    # Get data from Supabase
    token_usage_service = get_token_usage_service()
    if token_usage_service.is_connected():
        try:
            aggregated_data = token_usage_service.get_aggregated_token_usage(session_id)
            records = token_usage_service.get_token_usage_by_session(session_id)
            
            return {
                "all_token_usage": records,
                "total_token_usage": {
                    "call_count": aggregated_data.get("call_count", 0),
                    "total_tokens": aggregated_data.get("total_tokens", 0),
                    "total_prompt_tokens": aggregated_data.get("total_prompt_tokens", 0),
                    "total_response_tokens": aggregated_data.get("total_response_tokens", 0)
                },
                "source": "supabase"
            }
        except Exception as e:
            logger.error(f"Failed to get data from Supabase: {e}")
            return {
                "all_token_usage": [],
                "total_token_usage": {
                    "call_count": 0,
                    "total_tokens": 0,
                    "total_prompt_tokens": 0,
                    "total_response_tokens": 0
                },
                "source": "error"
            }
    else:
        logger.error("Supabase client not connected")
        return {
            "all_token_usage": [],
            "total_token_usage": {
                "call_count": 0,
                "total_tokens": 0,
                "total_prompt_tokens": 0,
                "total_response_tokens": 0
            },
            "source": "disconnected"
        }


# New Supabase-specific functions

def get_user_token_usage(user_id: str) -> list:
    """
    Get all token usage records for a specific user from Supabase.
    
    Args:
        user_id: The user ID to query
        
    Returns:
        list: List of token usage records
    """
    token_usage_service = get_token_usage_service()
    if not token_usage_service.is_connected():
        logger.error("Supabase client not connected")
        return []
    
    return token_usage_service.get_token_usage_by_user(user_id)


def get_agent_token_usage_from_db(agent_name: str) -> list:
    """
    Get all token usage records for a specific agent from Supabase.
    
    Args:
        agent_name: The agent name to query
        
    Returns:
        list: List of token usage records
    """
    token_usage_service = get_token_usage_service()
    if not token_usage_service.is_connected():
        logger.error("Supabase client not connected")
        return []
    
    return token_usage_service.get_token_usage_by_agent(agent_name)


def get_token_usage_by_timeframe_from_db(start_time: str, end_time: str) -> list:
    """
    Get token usage records within a specific timeframe from Supabase.
    
    Args:
        start_time: Start time in ISO format
        end_time: End time in ISO format
        
    Returns:
        list: List of token usage records
    """
    token_usage_service = get_token_usage_service()
    if not token_usage_service.is_connected():
        logger.error("Supabase client not connected")
        return []
    
    return token_usage_service.get_token_usage_by_timeframe(start_time, end_time)


def get_user_aggregated_usage(user_id: str) -> dict:
    """
    Get aggregated token usage for a specific user from Supabase.
    
    Args:
        user_id: The user ID to aggregate
        
    Returns:
        dict: Aggregated token usage data
    """
    token_usage_service = get_token_usage_service()
    if not token_usage_service.is_connected():
        logger.error("Supabase client not connected")
        return {}
    
    records = token_usage_service.get_token_usage_by_user(user_id)
    
    if not records:
        return {
            "total_prompt_tokens": 0,
            "total_response_tokens": 0,
            "total_thoughts_tokens": 0,
            "total_tool_use_tokens": 0,
            "total_tokens": 0,
            "call_count": 0,
            "sessions": {},
            "agents": {}
        }
    
    total_prompt = sum(record.get("prompt_tokens", 0) or 0 for record in records)
    total_response = sum(record.get("response_tokens", 0) or 0 for record in records)
    total_thoughts = sum(record.get("thoughts_tokens", 0) or 0 for record in records)
    total_tool_use = sum(record.get("tool_use_tokens", 0) or 0 for record in records)
    
    # Group by session
    sessions = {}
    for record in records:
        session_id = record.get("session_id", "unknown")
        if session_id not in sessions:
            sessions[session_id] = {
                "call_count": 0,
                "prompt_tokens": 0,
                "response_tokens": 0,
                "thoughts_tokens": 0,
                "tool_use_tokens": 0,
                "total_tokens": 0
            }
        
        sessions[session_id]["call_count"] += 1
        sessions[session_id]["prompt_tokens"] += record.get("prompt_tokens", 0) or 0
        sessions[session_id]["response_tokens"] += record.get("response_tokens", 0) or 0
        sessions[session_id]["thoughts_tokens"] += record.get("thoughts_tokens", 0) or 0
        sessions[session_id]["tool_use_tokens"] += record.get("tool_use_tokens", 0) or 0
        sessions[session_id]["total_tokens"] += (record.get("prompt_tokens", 0) or 0) + (record.get("response_tokens", 0) or 0)
    
    # Group by agent
    agents = {}
    for record in records:
        agent_name = record.get("agent_name", "unknown")
        if agent_name not in agents:
            agents[agent_name] = {
                "call_count": 0,
                "prompt_tokens": 0,
                "response_tokens": 0,
                "thoughts_tokens": 0,
                "tool_use_tokens": 0,
                "total_tokens": 0
            }
        
        agents[agent_name]["call_count"] += 1
        agents[agent_name]["prompt_tokens"] += record.get("prompt_tokens", 0) or 0
        agents[agent_name]["response_tokens"] += record.get("response_tokens", 0) or 0
        agents[agent_name]["thoughts_tokens"] += record.get("thoughts_tokens", 0) or 0
        agents[agent_name]["tool_use_tokens"] += record.get("tool_use_tokens", 0) or 0
        agents[agent_name]["total_tokens"] += (record.get("prompt_tokens", 0) or 0) + (record.get("response_tokens", 0) or 0)
    
    return {
        "total_prompt_tokens": total_prompt,
        "total_response_tokens": total_response,
        "total_thoughts_tokens": total_thoughts,
        "total_tool_use_tokens": total_tool_use,
        "total_tokens": total_prompt + total_response,
        "call_count": len(records),
        "sessions": sessions,
        "agents": agents
    }
