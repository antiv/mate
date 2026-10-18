"""
Fallback model: re-run a failed model call on a second model.

When an agent's own model raises (a provider outage, a timeout), the person
chatting — often on a customer's site through the widget — would otherwise see
the run fail. An agent configured with `fallback_model` re-runs that request
once on it instead.

ADK runs on_model_error callbacks with the request that failed and the context
it ran in, and a response returned from one is used in place of the error and
still goes through the after-model callbacks, so guardrails and token logging
apply to the fallback's answer as to any other.

The fallback is built from the model name alone, through the provider env vars.
It never inherits the agent's model_base_url / model_api_key: those belong to
the primary model, and the fallback is usually on a different provider, so
reusing them would send that key to the wrong host.

Only a provider outage falls back: a timeout, a connection failure, a 5xx or a
rate limit. Errors the request itself causes (a content-policy refusal, an
oversized context, a bad request) do not, or anyone chatting could move their
request to the fallback at will, past the primary provider's own filters.
"""

import copy
import logging
from typing import Any, Callable, Optional, Tuple, Type

import httpx
import litellm
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from .token_usage_callback import _get_adk_session_info

logger = logging.getLogger(__name__)

# What a provider outage raises. Both runtimes reach providers through LiteLLM,
# except ADK's native Gemini backend, which raises google.genai errors.
PROVIDER_OUTAGE_ERRORS: Tuple[Type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.NetworkError,
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.RateLimitError,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
    litellm.BadGatewayError,
)


def is_provider_outage(error: BaseException) -> bool:
    """True when `error` means the provider is unavailable, not that the request is at fault."""
    if isinstance(error, PROVIDER_OUTAGE_ERRORS):
        return True
    try:
        from google.genai.errors import ClientError, ServerError
    except ImportError:
        return False
    # ADK's Gemini backend raises a ClientError subclass for 429 (resource exhausted)
    return isinstance(error, ServerError) or (isinstance(error, ClientError) and error.code == 429)


def record_model_fallback(agent_name: Optional[str], user_id: Optional[str],
                          primary_model: Optional[str], fallback_model: str,
                          error: Optional[BaseException]) -> None:
    """Log a warning and an audit entry for a fallback that served a request. Never raises.

    Shared by both runtimes. LangGraph's fallback swallows the primary's error, so
    there `error` is None.
    """
    reason = f"{type(error).__name__}: {error}" if error is not None else None
    logger.warning(
        f"Agent '{agent_name}': model '{primary_model}' failed"
        f"{f' ({reason})' if reason else ''}; answered with fallback model '{fallback_model}'"
    )
    try:
        from ..utils.audit_service import ACTION_MODEL_FALLBACK, RESOURCE_AGENT, log
        log(user_id or "system", ACTION_MODEL_FALLBACK, RESOURCE_AGENT, resource_id=agent_name,
            details={
                "primary_model": primary_model,
                "fallback_model": fallback_model,
                "error": reason[:500] if reason else None,
            })
    except Exception as e:
        logger.warning(f"Audit entry for model fallback failed: {e}")


def make_model_fallback_callback(agent_name: str, primary_model: Optional[str],
                                 fallback_model: str) -> Callable[..., Any]:
    """Build an ADK on_model_error_callback that answers with `fallback_model`."""
    from ..utils.utils import create_model

    llm = create_model(model_name=fallback_model)

    async def model_fallback_callback(*, callback_context: CallbackContext,
                                      llm_request: LlmRequest,
                                      error: Exception) -> Optional[LlmResponse]:
        if not is_provider_outage(error):
            return None  # ADK re-raises it, as without a fallback
        response = None
        try:
            # Shallow, so the tools the request points at are not copied (they may
            # hold locks or clients that cannot be); what a model call can change
            # is copied, so the failed request is left as it was.
            request = llm_request.model_copy(update={
                "model": llm.model,
                "contents": copy.deepcopy(llm_request.contents),
                "config": llm_request.config.model_copy(deep=True) if llm_request.config else None,
            })
            # Not streamed: a callback returns one response. The final one carries
            # the whole answer and its usage.
            async for response in llm.generate_content_async(request, stream=False):
                pass
        except Exception as fallback_error:
            logger.error(
                f"Agent '{agent_name}': fallback model '{fallback_model}' failed too "
                f"({type(fallback_error).__name__}: {fallback_error})"
            )
            return None  # ADK re-raises the primary model's error
        if response is None:
            return None

        # Token logging reads the model from state; without this the fallback's
        # tokens would be booked against the model that failed. The flag lets the
        # Usage page show what the fallback cost apart.
        try:
            callback_context.state['current_model_name'] = fallback_model
            callback_context.state['current_model_is_fallback'] = True
        except Exception:
            pass
        user_id, _ = _get_adk_session_info(callback_context)
        record_model_fallback(agent_name, user_id, primary_model, fallback_model, error)
        return response

    return model_fallback_callback
