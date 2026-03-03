"""
User profile callback for automatically injecting user profile data into agent context.

This callback runs before model execution to automatically add user profile information
to the agent's context, so the agent has immediate access to user information without
needing to call get_user_profile() tool.
"""

import logging
from typing import Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from ..utils.user_service import get_user_service

logger = logging.getLogger(__name__)


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


def user_profile_context_callback(
    callback_context: CallbackContext, 
    llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Callback that automatically injects user profile data into the agent context.
    
    This callback:
    1. Extracts user_id from callback context
    2. Retrieves user profile data from database
    3. Injects profile data as a system message at the beginning of the conversation
    
    Args:
        callback_context: The callback context containing session information
        llm_request: The LLM request being processed
        
    Returns:
        None to continue normally (modifies llm_request in place)
    """
    try:
        # Extract user information
        user_id = _get_user_id(callback_context)
        if not user_id:
            logger.debug("No user ID found in context, skipping user profile injection")
            return None
        
        # Get user profile
        user_service = get_user_service()
        profile_data = user_service.get_user_profile(user_id)
        
        if not profile_data:
            logger.debug(f"No profile data found for user {user_id}, skipping injection")
            return None
        
        # Check if we've already injected profile data in this session
        # Use state to track if we've already added profile
        if not hasattr(callback_context, 'state'):
            callback_context.state = {}
        
        profile_key = f'user_profile_injected_{user_id}'
        if callback_context.state.get(profile_key):
            # Already injected in this session, skip
            logger.debug(f"User profile already injected for user {user_id} in this session")
            return None
        
        # Prepare user profile content
        profile_content = f"""== USER PROFILE ==

{profile_data}

== END USER PROFILE ==

Use this information to personalize your responses. You can update this profile using the update_user_profile tool when you learn new information about the user."""
        
        # Inject profile as a system message at the beginning
        # Check if llm_request has contents
        if not hasattr(llm_request, 'contents') or llm_request.contents is None:
            # Initialize contents if it doesn't exist
            llm_request.contents = []
        
        # Create system message with user profile
        system_message = types.Content(
            role="system",
            parts=[types.Part(text=profile_content)]
        )
        
        # Insert at the beginning of contents (before any user messages)
        # Check if there's already a system message - if so, prepend to it
        has_system_message = False
        for i, content in enumerate(llm_request.contents):
            if content.role == "system":
                # Prepend user profile to existing system message
                # Safely extract existing text with defensive checks
                existing_text = ""
                if content.parts and len(content.parts) > 0:
                    first_part = content.parts[0]
                    if hasattr(first_part, 'text') and first_part.text:
                        existing_text = first_part.text
                combined_text = f"{profile_content}\n\n---\n\n{existing_text}"
                llm_request.contents[i] = types.Content(
                    role="system",
                    parts=[types.Part(text=combined_text)]
                )
                has_system_message = True
                break
        
        if not has_system_message:
            # Insert system message at the beginning
            llm_request.contents.insert(0, system_message)
        
        # Mark as injected in state
        callback_context.state[profile_key] = True
        logger.debug(f"Injected user profile for user {user_id} into agent context")
        
        return None  # Continue with normal processing
        
    except Exception as e:
        logger.error(f"Error injecting user profile into context: {e}", exc_info=True)
        # Don't block the request if profile injection fails
        return None


def combined_user_profile_and_rbac_callback(
    callback_context: CallbackContext, 
    llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """
    Combined callback that:
    1. Captures model name
    2. Injects user profile into context
    3. Performs RBAC check
    
    This ensures user profile is available and all necessary checks are performed.
    
    Args:
        callback_context: The callback context
        llm_request: The LLM request being processed
        
    Returns:
        LlmResponse if access denied, None to continue normally
    """
    # First capture model name (always needed for token tracking)
    try:
        from .token_usage_callback import capture_model_name_callback
        capture_model_name_callback(callback_context, llm_request)
    except Exception as e:
        logger.warning(f"Error in token usage callback: {e}")
    
    # Then inject user profile
    try:
        user_profile_context_callback(callback_context, llm_request)
    except Exception as e:
        logger.warning(f"Error in user profile callback: {e}")
    
    # Then perform RBAC check
    try:
        from .rbac_callback import rbac_before_model_callback
        rbac_response = rbac_before_model_callback(callback_context, llm_request)
        if rbac_response:
            return rbac_response  # Access denied
    except Exception as e:
        logger.warning(f"Error in RBAC callback: {e}")

    # Finally run input guardrails (PII, prompt injection, content policy, etc.)
    try:
        from .guardrail_callback import guardrail_before_model_callback
        guardrail_response = guardrail_before_model_callback(callback_context, llm_request)
        if guardrail_response:
            return guardrail_response  # Blocked by guardrail
    except Exception as e:
        logger.warning(f"Error in guardrail before_model callback: {e}")

    return None

