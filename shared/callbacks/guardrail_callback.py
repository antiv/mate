"""
Guardrail callbacks for before_model and after_model hooks.

These callbacks integrate the GuardrailEngine with ADK's callback system.
They read the guardrail_config from the agent's DB config and run
input/output checks, logging triggers and optionally blocking responses.
"""

import logging
import uuid
from typing import Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from ..utils.guardrails import GuardrailEngine, GuardrailAction

logger = logging.getLogger(__name__)


def _extract_user_text_from_request(llm_request: LlmRequest) -> str:
    """Extract the latest user message text from an LLM request."""
    if not llm_request.contents:
        return ""
    for content in reversed(llm_request.contents):
        if content.role == "user" and content.parts:
            texts = []
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    texts.append(part.text)
            if texts:
                return "\n".join(texts)
    return ""


def _extract_text_from_response(llm_response: LlmResponse) -> str:
    """Extract text from an LLM response."""
    if not llm_response.content:
        return ""
    if not llm_response.content.parts:
        return ""
    texts = []
    for part in llm_response.content.parts:
        if hasattr(part, "text") and part.text:
            texts.append(part.text)
    return "\n".join(texts)


def _get_context_metadata(callback_context: CallbackContext) -> dict:
    """Extract request_id, session_id, user_id from callback context."""
    meta = {"request_id": str(uuid.uuid4()), "session_id": None, "user_id": None}

    if hasattr(callback_context, "_invocation_context") and callback_context._invocation_context:
        inv = callback_context._invocation_context
        meta["user_id"] = getattr(inv, "user_id", None)
        if hasattr(inv, "session") and inv.session:
            meta["session_id"] = getattr(inv.session, "id", None)

    if not meta["user_id"]:
        meta["user_id"] = getattr(callback_context, "user_id", None)

    return meta


def _get_guardrail_engine(callback_context: CallbackContext) -> Optional[GuardrailEngine]:
    """
    Load the GuardrailEngine for the current agent from DB config.
    Does not cache in session state (state gets serialized; GuardrailEngine is not JSON-serializable).
    """
    agent_name = getattr(callback_context, "agent_name", None)
    if not agent_name:
        if hasattr(callback_context, "agent") and callback_context.agent:
            agent_name = getattr(callback_context.agent, "name", None)
    if not agent_name:
        return None

    try:
        from ..utils.agent_manager import get_agent_manager
        agent_manager = get_agent_manager()
        session = agent_manager.get_session()
        if not session:
            return None
        try:
            from ..utils.models import AgentConfig
            config = session.query(AgentConfig).filter(
                AgentConfig.name == agent_name,
                AgentConfig.disabled.is_(False),
            ).first()
            if config and config.guardrail_config:
                engine = GuardrailEngine.from_json(config.guardrail_config)
                if engine.has_guardrails:
                    return engine
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error loading guardrail config for {agent_name}: {e}")

    return None


def _log_guardrail_results(results, agent_name, meta):
    """Log triggered guardrail results to DB."""
    try:
        from ..utils.guardrail_log_service import get_guardrail_log_service
        svc = get_guardrail_log_service()
        for r in results:
            if r.triggered:
                svc.log_trigger(
                    request_id=meta["request_id"],
                    session_id=meta["session_id"],
                    user_id=meta["user_id"],
                    agent_name=agent_name,
                    guardrail_type=r.guardrail_type,
                    phase=r.phase.value,
                    action_taken=r.action.value,
                    matched_content=r.matched_content,
                    details=r.details,
                )
    except Exception as e:
        logger.warning(f"Failed to log guardrail results: {e}")


def guardrail_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
    """
    Input guardrail: runs before the model call.
    If action=block and triggered, returns an LlmResponse that prevents the call.
    If action=redact, modifies the request text in place.
    """
    try:
        engine = _get_guardrail_engine(callback_context)
        if not engine:
            return None

        user_text = _extract_user_text_from_request(llm_request)
        if not user_text:
            return None

        summary = engine.check_input(user_text)
        if not summary.triggered_results:
            return None

        agent_name = getattr(callback_context, "agent_name", "unknown")
        meta = _get_context_metadata(callback_context)

        # Log all triggered results
        _log_guardrail_results(summary.triggered_results, agent_name, meta)

        # Handle block action
        if summary.should_block:
            reasons = "; ".join(summary.block_reasons)
            logger.warning(f"Guardrail BLOCK on input for agent {agent_name}: {reasons}")
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=f"I cannot process this request. Guardrail triggered: {reasons}")],
                )
            )

        # Handle redact action — modify the last user message in-place
        for result in summary.triggered_results:
            if result.action == GuardrailAction.REDACT and result.redacted_text:
                _apply_redaction_to_request(llm_request, result.redacted_text)
                break

        # Warn/log actions: just log (already done above), continue
        for result in summary.triggered_results:
            if result.action == GuardrailAction.WARN:
                logger.warning(f"Guardrail WARNING on input for agent {agent_name}: {result.details}")

        return None

    except Exception as e:
        logger.error(f"Error in guardrail before_model callback: {e}")
        return None


def guardrail_after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> Optional[LlmResponse]:
    """
    Output guardrail: runs after the model call.
    If action=block, replaces the response with a safe message.
    If action=redact, modifies the response text.
    """
    try:
        engine = _get_guardrail_engine(callback_context)
        if not engine:
            return None

        response_text = _extract_text_from_response(llm_response)
        if not response_text:
            return None

        summary = engine.check_output(response_text)
        if not summary.triggered_results:
            return None

        agent_name = getattr(callback_context, "agent_name", "unknown")
        meta = _get_context_metadata(callback_context)

        _log_guardrail_results(summary.triggered_results, agent_name, meta)

        # Handle block
        if summary.should_block:
            reasons = "; ".join(summary.block_reasons)
            logger.warning(f"Guardrail BLOCK on output for agent {agent_name}: {reasons}")
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=f"Response blocked by safety guardrail: {reasons}")],
                ),
                usage_metadata=llm_response.usage_metadata,
            )

        # Handle redact — replace response text
        for result in summary.triggered_results:
            if result.action == GuardrailAction.REDACT and result.redacted_text:
                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=result.redacted_text)],
                    ),
                    usage_metadata=llm_response.usage_metadata,
                )

        # Warn/log only
        for result in summary.triggered_results:
            if result.action == GuardrailAction.WARN:
                logger.warning(f"Guardrail WARNING on output for agent {agent_name}: {result.details}")

        return None

    except Exception as e:
        logger.error(f"Error in guardrail after_model callback: {e}")
        return None


def _apply_redaction_to_request(llm_request: LlmRequest, redacted_text: str):
    """Replace the last user message with redacted text."""
    if not llm_request.contents:
        return
    for content in reversed(llm_request.contents):
        if content.role == "user" and content.parts:
            content.parts = [types.Part(text=redacted_text)]
            return
