"""
Cross-cutting hooks for the LangGraph run loop: RBAC, guardrails and user
profile injection.

These call the same ADK-free services the ADK callbacks delegate to
(rbac_middleware, user_service, guardrails engine, guardrail_log_service), so
behavior and log records match the ADK runtime.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def check_rbac(user_id: str, agent_name: str, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """RBAC check for one agent. Returns an ADK-shaped error event when denied, None when allowed.

    Mirrors shared/callbacks/rbac_callback.py: gets/creates the user, loads the
    agent's allowed_for_roles, audits denials and logs an ACCESS_DENIED token event.
    """
    try:
        from shared.utils.user_service import get_user_service
        from shared.utils.rbac_middleware import get_rbac_middleware

        user = get_user_service().get_or_create_user(user_id)
        if not user:
            logger.error(f"Failed to get/create user {user_id}")
            return _access_denied_event("Authentication failed. Please try again.", user_id, agent_name)
        # Read roles now — the ORM instance detaches once the service session closes
        try:
            user_roles = user.get_roles()
        except Exception:
            user_roles = []

        agent_config = _load_agent_config_row(agent_name)
        if not agent_config:
            logger.warning(f"Agent config not found for {agent_name}, allowing access")
            return None

        config_dict = {"name": agent_config["name"], "allowed_for_roles": []}
        if agent_config.get("allowed_for_roles"):
            try:
                config_dict["allowed_for_roles"] = json.loads(agent_config["allowed_for_roles"])
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Invalid JSON in allowed_for_roles for agent {agent_name}")

        has_access, error_message = get_rbac_middleware().check_agent_access(user_id, config_dict)
        if has_access:
            logger.debug(f"RBAC: Access granted for user {user_id} to agent {agent_name}")
            return None

        required_roles = config_dict.get("allowed_for_roles", [])
        logger.error(f"RBAC: Access DENIED for user '{user_id}' to agent '{agent_name}'. "
                     f"User roles: {user_roles}, Required: {required_roles}")

        try:
            from shared.utils.audit_service import log, ACTION_RBAC_DENIAL, RESOURCE_AGENT
            log(user_id, ACTION_RBAC_DENIAL, RESOURCE_AGENT, resource_id=agent_name,
                details={"user_roles": user_roles, "required_roles": required_roles}, request=None)
        except Exception as e:
            logger.debug(f"Audit log RBAC denial: {e}")

        try:
            from shared.utils.token_usage_service import get_token_usage_service
            get_token_usage_service().log_token_usage(
                request_id=str(uuid.uuid4()),
                session_id=session_id,
                user_id=user_id,
                agent_name=agent_name,
                model_name="RBAC_CHECK",
                prompt_tokens=0, response_tokens=0, thoughts_tokens=0, tool_use_tokens=0,
                status="ACCESS_DENIED",
                error_description=f"Access denied to agent '{agent_name}'. "
                                  f"User roles: {user_roles}, Required roles: {required_roles}",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning(f"Failed to log access denied event: {e}")

        return _access_denied_event(error_message or "Insufficient permissions",
                                    user_id, agent_name, user_roles, required_roles)
    except Exception as e:
        logger.error(f"Error in RBAC hook: {e}")
        # Match the ADK callback's fail-open behavior
        return None


def check_rbac_message(user_id: str, agent_name: str) -> Optional[str]:
    """RBAC as a plain message — used inside the transfer_to_agent tool."""
    event = check_rbac(user_id, agent_name)
    return event.get("error_message") if event else None


def _access_denied_event(message: str, user_id: str, agent_name: str,
                         user_roles: Optional[list] = None,
                         required_roles: Optional[list] = None) -> Dict[str, Any]:
    detailed = f"🚫 Access Denied: {message}"
    if user_roles and required_roles:
        detailed += f" | Agent: {agent_name} | Your roles: {user_roles} | Required roles: {required_roles}"
    detailed += f" | User ID: {user_id} | Contact your administrator to request access or update your roles."
    return {
        "id": str(uuid.uuid4()),
        "author": agent_name,
        "error_code": "RBAC_ACCESS_DENIED",
        "error_message": detailed,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }


def _load_agent_config_row(agent_name: str) -> Optional[Dict[str, Any]]:
    from shared.utils.database_client import get_database_client
    from shared.utils.models import AgentConfig
    db_client = get_database_client()
    session = db_client.get_session() if db_client else None
    if not session:
        return None
    try:
        row = session.query(AgentConfig).filter(
            AgentConfig.name == agent_name,
            AgentConfig.disabled.is_(False)
        ).first()
        return row.to_dict() if row else None
    finally:
        session.close()


# --- Guardrails -------------------------------------------------------------

def build_guardrail_engines(configs_by_agent: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """agent name → GuardrailEngine, for agents that have guardrails configured."""
    from shared.utils.guardrails.engine import GuardrailEngine
    engines = {}
    for agent_name, config in configs_by_agent.items():
        raw = config.get("guardrail_config")
        if not raw:
            continue
        try:
            engine = GuardrailEngine.from_json(raw if isinstance(raw, str) else json.dumps(raw))
            if engine.has_guardrails:
                engines[agent_name] = engine
        except Exception as e:
            logger.error(f"Error loading guardrail config for {agent_name}: {e}")
    return engines


def _log_guardrail_results(results, agent_name: str, meta: Dict[str, Any]) -> None:
    try:
        from shared.utils.guardrail_log_service import get_guardrail_log_service
        svc = get_guardrail_log_service()
        for result in results:
            if result.triggered:
                svc.log_trigger(
                    request_id=meta.get("request_id"),
                    session_id=meta.get("session_id"),
                    user_id=meta.get("user_id"),
                    agent_name=agent_name,
                    guardrail_type=result.guardrail_type,
                    phase=result.phase.value,
                    action_taken=result.action.value,
                    matched_content=result.matched_content,
                    details=result.details,
                )
    except Exception as e:
        logger.warning(f"Failed to log guardrail results: {e}")


def check_input_guardrails(engine: Any, text: str, agent_name: str,
                           meta: Dict[str, Any]) -> Optional[str]:
    """Returns the block message when input is blocked; None to continue.

    Redaction is applied by mutating meta['redacted_text'] (caller replaces input).
    """
    if not engine or not text:
        return None
    summary = engine.check_input(text)
    if not summary.triggered_results:
        return None
    _log_guardrail_results(summary.triggered_results, agent_name, meta)
    if summary.should_block:
        reasons = "; ".join(summary.block_reasons)
        logger.warning(f"Guardrail BLOCK on input for agent {agent_name}: {reasons}")
        return f"I cannot process this request. Guardrail triggered: {reasons}"
    from shared.utils.guardrails.base import GuardrailAction
    for result in summary.triggered_results:
        if result.action == GuardrailAction.REDACT and result.redacted_text:
            meta["redacted_text"] = result.redacted_text
            break
    return None


def check_output_guardrails(engine: Any, text: str, agent_name: str,
                            meta: Dict[str, Any]) -> Optional[str]:
    """Returns replacement text when output must be blocked/redacted; None to keep it."""
    if not engine or not text:
        return None
    summary = engine.check_output(text)
    if not summary.triggered_results:
        return None
    _log_guardrail_results(summary.triggered_results, agent_name, meta)
    if summary.should_block:
        reasons = "; ".join(summary.block_reasons)
        logger.warning(f"Guardrail BLOCK on output for agent {agent_name}: {reasons}")
        return f"Response blocked by safety guardrail: {reasons}"
    from shared.utils.guardrails.base import GuardrailAction
    for result in summary.triggered_results:
        if result.action == GuardrailAction.REDACT and result.redacted_text:
            return result.redacted_text
    return None


# --- User profile injection ---------------------------------------------------

def get_user_profile_block(user_id: Optional[str]) -> Optional[str]:
    """The profile block appended to the system prompt (same text as user_profile_callback)."""
    if not user_id:
        return None
    try:
        from shared.utils.user_service import get_user_service
        profile_data = get_user_service().get_user_profile(user_id)
        if not profile_data:
            return None
        return (
            "== USER PROFILE ==\n\n"
            f"{profile_data}\n\n"
            "Use this information to personalize your responses. You can update this "
            "profile using the update_user_profile tool when you learn new information about the user."
        )
    except Exception as e:
        logger.debug(f"User profile injection skipped: {e}")
        return None
