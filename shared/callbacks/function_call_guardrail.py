"""
Function call validation guardrail to prevent malformed function calls.
"""

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def validate_function_calls_guardrail(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """
    Validates function calls in LLM responses to prevent empty function names.
    
    Args:
        callback_context: The callback context containing session state
        llm_response: The LLM response containing function calls
        
    Returns:
        None to continue normal processing, or LlmResponse to override
    """
    if not hasattr(llm_response, 'function_calls') or not llm_response.function_calls:
        return None
    
    # Log function calls for debugging
    logger.info(f"Agent '{callback_context.agent_name}' has {len(llm_response.function_calls)} function calls")
    for i, fc in enumerate(llm_response.function_calls):
        try:
            name = getattr(fc, 'name', 'NO_NAME_ATTR')
            args = getattr(fc, 'args', 'N/A')
            logger.info(f"Function call {i}: name='{name}', args={args}")
        except Exception as e:
            logger.error(f"Error logging function call {i}: {e}")
            logger.error(f"Function call object: {fc}")
            logger.error(f"Function call type: {type(fc)}")
    
    # Check for empty or malformed function names
    valid_function_calls = []
    
    for function_call in llm_response.function_calls:
        try:
            # Check if function call object is valid
            if not function_call:
                logger.error(f"Null function call object detected in agent '{callback_context.agent_name}'")
                continue
            
            # Check if function name exists and is not empty
            if not hasattr(function_call, 'name') or not function_call.name or function_call.name.strip() == "":
                logger.error(f"Empty or missing function name detected in agent '{callback_context.agent_name}'")
                logger.error(f"Function call: {function_call}")
                logger.error(f"Function call attributes: {dir(function_call)}")
                
                # Skip this function call instead of returning an error
                continue
            
            # Check if function name is just whitespace
            if function_call.name.strip() == "":
                logger.error(f"Whitespace-only function name detected in agent '{callback_context.agent_name}'")
                logger.error(f"Function call: {function_call}")
                continue
                
            valid_function_calls.append(function_call)
            
        except Exception as e:
            logger.error(f"Error processing function call in agent '{callback_context.agent_name}': {e}")
            logger.error(f"Function call: {function_call}")
            logger.error(f"Function call type: {type(function_call)}")
            continue
    
    # If we filtered out invalid function calls, create a new response
    if len(valid_function_calls) != len(llm_response.function_calls):
        logger.info(f"Filtered out {len(llm_response.function_calls) - len(valid_function_calls)} invalid function calls")
        
        # If all function calls were invalid, return a response with no function calls
        if len(valid_function_calls) == 0:
            logger.warning(f"All function calls were invalid for agent '{callback_context.agent_name}', returning response without function calls")
            filtered_response = LlmResponse(
                content=llm_response.content,
                function_calls=[],  # Empty list instead of None
                usage_metadata=llm_response.usage_metadata
            )
        else:
            # Create a new response with only valid function calls
            filtered_response = LlmResponse(
                content=llm_response.content,
                function_calls=valid_function_calls,
                usage_metadata=llm_response.usage_metadata
            )
        
        return filtered_response
    
    return None  # Continue with normal processing


def combined_after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """
    Combined callback that validates function calls and tracks token usage.
    
    Args:
        callback_context: The callback context containing session state
        llm_response: The LLM response containing function calls
        
    Returns:
        None to continue normal processing, or LlmResponse to override
    """
    # First validate function calls
    validation_result = validate_function_calls_guardrail(callback_context, llm_response)
    if validation_result is not None:
        return validation_result
    
    # Then track token usage
    from .token_usage_callback import log_token_usage_callback
    return log_token_usage_callback(callback_context, llm_response)
