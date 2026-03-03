"""
RBAC callback for enforcing role-based access control on agent requests.

This callback runs before model execution to check if the user has permission
to access the requested agent based on their roles.
"""

import logging
from typing import Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from ..utils.user_service import get_user_service
from ..utils.rbac_middleware import get_rbac_middleware, AccessDeniedException

logger = logging.getLogger(__name__)


def rbac_before_model_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    """
    RBAC callback that runs before model execution to check permissions.
    
    This callback:
    1. Extracts session ID from the callback context
    2. Gets or creates user from session
    3. Checks if user has permission to access the current agent
    4. Returns an error response if access is denied
    
    Args:
        callback_context: The callback context containing session information
        llm_request: The LLM request being processed
        
    Returns:
        LlmResponse with error message if access denied, None to continue normally
    """
    span = None
    try:
        from shared.utils.tracing.tracing_config import is_tracing_enabled
        if is_tracing_enabled():
            from opentelemetry import trace
            from shared.utils.tracing.tracer import get_tracer
            from shared.utils.tracing.genai_attributes import MATE_AGENT_NAME, MATE_USER_ID, MATE_RBAC_ALLOWED
            tracer = get_tracer("mate", "1.0.0")
            span = tracer.start_span("mate.rbac_check")
            logger.debug(
                "trace: mate.rbac_check span started agent=%s",
                getattr(callback_context, "agent_name", None),
            )
            agent_name = getattr(callback_context, 'agent_name', None) or (
                getattr(callback_context.agent, 'name', None) if hasattr(callback_context, 'agent') and callback_context.agent else None
            )
            user_id = _get_user_id(callback_context)
            if agent_name:
                span.set_attribute(MATE_AGENT_NAME, agent_name)
            if user_id:
                span.set_attribute(MATE_USER_ID, user_id)
    except Exception:
        pass

    try:
        # Extract user information
        user_id = _get_user_id(callback_context)
        if not user_id:
            if span:
                try:
                    span.set_attribute("mate.rbac.allowed", True)
                    logger.debug("trace: mate.rbac_check span ended allowed=True")
                    span.end()
                except Exception:
                    pass
            logger.warning("No user ID found, allowing access (fallback behavior)")
            return None

        # Get agent information from context
        agent_name = getattr(callback_context, 'agent_name', None)
        if not agent_name and hasattr(callback_context, 'agent') and callback_context.agent:
            agent_name = getattr(callback_context.agent, 'name', None)
        
        if not agent_name:
            if span:
                try:
                    span.set_attribute("mate.rbac.allowed", True)
                    logger.debug("trace: mate.rbac_check span ended allowed=True")
                    span.end()
                except Exception:
                    pass
            logger.warning("No agent name found in context, allowing access")
            return None

        # Skip RBAC check for certain system agents or if no restrictions
        if _should_skip_rbac_check(agent_name):
            if span:
                try:
                    span.set_attribute("mate.rbac.allowed", True)
                    logger.debug("trace: mate.rbac_check span ended allowed=True")
                    span.end()
                except Exception:
                    pass
            logger.debug(f"Skipping RBAC check for agent: {agent_name}")
            return None
        
        # Get user service and RBAC middleware
        user_service = get_user_service()
        rbac_middleware = get_rbac_middleware()
        
        # Get or create user (this will create user with default 'user' role if not exists)
        user = user_service.get_or_create_user(user_id)
        if not user:
            if span:
                try:
                    span.set_attribute("mate.rbac.allowed", False)
                    logger.debug("trace: mate.rbac_check span ended allowed=False")
                    span.end()
                except Exception:
                    pass
            logger.error(f"Failed to get/create user {user_id}")
            return _create_access_denied_response(
                "Authentication failed. Please try again.",
                user_id, agent_name
            )
        
        # Get agent configuration to check allowed roles
        from ..utils.agent_manager import get_agent_manager
        agent_manager = get_agent_manager()
        
        # Try to get agent config (this might be a subagent, so check both root and subagents)
        agent_config = None
        try:
            # First try as root agent
            agent_config = agent_manager.get_root_agent_by_name(agent_name)
            
            # If not found as root, search through all agent configs
            if not agent_config:
                session = agent_manager.get_session()
                if session:
                    try:
                        from ..utils.models import AgentConfig
                        agent_config = session.query(AgentConfig).filter(
                            AgentConfig.name == agent_name,
                            AgentConfig.disabled.is_(False)
                        ).first()
                    finally:
                        session.close()
        except Exception as e:
            logger.error(f"Error getting agent config for {agent_name}: {e}")
        
        if not agent_config:
            if span:
                try:
                    span.set_attribute("mate.rbac.allowed", True)
                    logger.debug("trace: mate.rbac_check span ended allowed=True")
                    span.end()
                except Exception:
                    pass
            logger.warning(f"Agent config not found for {agent_name}, allowing access")
            return None
        
        # Check RBAC permissions
        config_dict = {
            'name': agent_config.name,
            'allowed_for_roles': []
        }
        
        # Parse allowed roles if present
        if agent_config.allowed_for_roles:
            try:
                import json
                config_dict['allowed_for_roles'] = json.loads(agent_config.allowed_for_roles)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in allowed_for_roles for agent {agent_name}")
        
        # Check access
        has_access, error_message = rbac_middleware.check_agent_access(user_id, config_dict)
        
        if not has_access:
            if span:
                try:
                    span.set_attribute("mate.rbac.allowed", False)
                    logger.debug("trace: mate.rbac_check span ended allowed=False")
                    span.end()
                except Exception:
                    pass
            user_roles = user.get_roles()
            required_roles = config_dict.get('allowed_for_roles', [])
            logger.error(f"RBAC: Access DENIED for user '{user_id}' to agent '{agent_name}'. User roles: {user_roles}, Required: {required_roles}")
            
            # Log this access denial as a token usage event for tracking
            _log_access_denied_event(callback_context, user_id, agent_name, user_roles, required_roles)
            
            return _create_access_denied_response(
                error_message or "Insufficient permissions",
                user_id, agent_name, user_roles, required_roles
            )
        
        # Access granted, log success
        if span:
            try:
                span.set_attribute("mate.rbac.allowed", True)
                span.end()
            except Exception:
                pass
        logger.debug(f"RBAC: Access granted for user {user_id} to agent {agent_name}")
        return None  # Continue with normal processing

    except Exception as e:
        if span:
            try:
                span.set_attribute("mate.rbac.allowed", True)
                logger.debug("trace: mate.rbac_check span ended allowed=True (exception path)")
                span.end()
            except Exception:
                pass
        logger.error(f"Error in RBAC callback: {e}")
        # In case of errors, allow access to prevent system breakage
        return None


def _log_access_denied_event(callback_context: CallbackContext, user_id: str, agent_name: str, 
                            user_roles: list, required_roles: list):
    """
    Log an access denied event to the token usage system for tracking.
    
    Args:
        callback_context: The callback context
        user_id: User ID that was denied access
        agent_name: Name of the agent that was accessed
        user_roles: User's current roles
        required_roles: Required roles for access
    """
    try:
        from ..utils.token_usage_service import get_token_usage_service
        import uuid
        from datetime import datetime, timezone
        
        # Generate a unique request ID for this access denial
        request_id = str(uuid.uuid4())
        
        # Get session ID if available (for backward compatibility)
        session_id = None
        if hasattr(callback_context, '_invocation_context') and callback_context._invocation_context:
            if hasattr(callback_context._invocation_context, 'session') and callback_context._invocation_context.session:
                session_id = getattr(callback_context._invocation_context.session, 'id', None)
        
        if not session_id:
            session_id = getattr(callback_context, 'session_id', request_id)  # Fallback to request_id
        
        # Create a token usage log entry for the access denial
        token_service = get_token_usage_service()
        
        # Try to get the model name from session state (captured by token callback)
        model_name = "RBAC_CHECK"
        try:
            if hasattr(callback_context, '_invocation_context') and callback_context._invocation_context:
                if hasattr(callback_context._invocation_context, 'session') and callback_context._invocation_context.session:
                    session_state = getattr(callback_context._invocation_context.session, 'state', {})
                    if isinstance(session_state, dict):
                        captured_model = session_state.get('current_model_name')
                        if captured_model:
                            model_name = captured_model  # Keep model name clean, status field will indicate error
        except Exception:
            pass  # Use default model_name if extraction fails
        
        # Create error description
        error_desc = f"Access denied to agent '{agent_name}'. User roles: {user_roles}, Required roles: {required_roles}"
        
        # Log with special values to indicate this was an access denial
        token_service.log_token_usage(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            agent_name=agent_name,  # Keep original agent name clean
            model_name=model_name,
            prompt_tokens=0,
            response_tokens=0,
            thoughts_tokens=0,
            tool_use_tokens=0,
            status='ACCESS_DENIED',
            error_description=error_desc,
            timestamp=datetime.now(timezone.utc)
        )
        
        logger.info(f"Logged access denial event: user={user_id}, agent={agent_name}, request_id={request_id}")
        
    except Exception as e:
        logger.warning(f"Failed to log access denied event: {e}")
        # Don't fail the RBAC check if logging fails


def _get_user_id(callback_context: CallbackContext) -> Optional[str]:
    """
    Get user ID from ADK callback context.
    
    Args:
        callback_context: The callback context
        
    Returns:
        User ID if found, None otherwise
    """
    # Try to get user_id from invocation context first (proper ADK way)
    if hasattr(callback_context, '_invocation_context') and callback_context._invocation_context:
        user_id = getattr(callback_context._invocation_context, 'user_id', None)
        if user_id:
            return user_id
    
    # Fallback to direct context attributes
    user_id = getattr(callback_context, 'user_id', None)
    if user_id:
        return user_id
    
    # Additional fallback attempts
    if hasattr(callback_context, 'session') and callback_context.session:
        user_id = getattr(callback_context.session, 'user_id', None)
        if user_id:
            return user_id
    
    return None


def _should_skip_rbac_check(agent_name: str) -> bool:
    """
    Determine if RBAC check should be skipped for this agent.
    
    Args:
        agent_name: Name of the agent
        
    Returns:
        True if RBAC check should be skipped, False otherwise
    """
    # Skip RBAC for fallback agents or system agents
    skip_agents = [
        'mate_fallback_agent',
        'system_agent',
        'health_check_agent'
    ]
    
    return agent_name in skip_agents


def _create_access_denied_response(message: str, user_id: str, agent_name: str, 
                                 user_roles: list = None, required_roles: list = None) -> LlmResponse:
    """
    Create an access denied response.
    
    Args:
        message: Error message
        user_id: User ID
        agent_name: Name of the agent being accessed
        user_roles: User's current roles
        required_roles: Required roles for access
        
    Returns:
        LlmResponse with access denied error
    """
    detailed_message = f"🚫 Access Denied: {message}"
    
    if user_roles and required_roles:
        detailed_message += f" | Agent: {agent_name} | Your roles: {user_roles} | Required roles: {required_roles}"
    
    detailed_message += f" | User ID: {user_id} | Contact your administrator to request access or update your roles."
    
    return LlmResponse(
        error_code="RBAC_ACCESS_DENIED",
        error_message=detailed_message
    )


def combined_rbac_and_token_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    """
    Combined callback that performs RBAC check and then token usage capture.
    
    This is a convenience callback that combines RBAC checking with the existing
    token usage capture functionality.
    
    Args:
        callback_context: The callback context
        llm_request: The LLM request being processed
        
    Returns:
        LlmResponse if access denied, None to continue normally
    """
    # Always capture model name first (even if access will be denied)
    try:
        from .token_usage_callback import capture_model_name_callback
        capture_model_name_callback(callback_context, llm_request)
    except Exception as e:
        logger.warning(f"Error in token usage callback: {e}")
    
    # Then perform RBAC check
    rbac_response = rbac_before_model_callback(callback_context, llm_request)
    if rbac_response:
        return rbac_response  # Access denied, but model name was captured
    
    # If RBAC passed, continue normally
    return None
