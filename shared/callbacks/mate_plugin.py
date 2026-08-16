"""
MATE plugin: app-wide RBAC, guardrails, user profile and token tracking.

ADK Plugins run for every agent in the App — including agents created at
runtime via create_agent_tool — so registering this plugin replaces the
per-agent before/after model callback wiring in agent_manager.

Opt-in via MATE_PLUGINS_ENABLED=true. When enabled, agent_manager skips
attaching per-agent model callbacks to avoid double execution.
"""

import logging
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from .error_callback import record_model_error_callback
from .guardrail_callback import guardrail_after_model_callback
from .token_usage_callback import log_token_usage_callback
from .user_profile_callback import combined_user_profile_and_rbac_callback

logger = logging.getLogger(__name__)


class MatePlugin(BasePlugin):
    """App-wide plugin delegating to the existing MATE callback chain."""

    def __init__(self, name: str = "mate_plugin"):
        super().__init__(name)

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        # Model name capture + user profile injection + RBAC + input guardrails
        return combined_user_profile_and_rbac_callback(callback_context, llm_request)

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        # Output guardrails, then token usage logging
        guardrail_result = guardrail_after_model_callback(callback_context, llm_response)
        if guardrail_result is not None:
            log_token_usage_callback(callback_context, guardrail_result)
            return guardrail_result
        return log_token_usage_callback(callback_context, llm_response)

    async def on_model_error_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest,
        error: Exception
    ) -> Optional[LlmResponse]:
        # Record the failure and return None so ADK re-raises the original error
        return record_model_error_callback(
            callback_context=callback_context, llm_request=llm_request, error=error
        )
