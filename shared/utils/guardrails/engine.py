"""
GuardrailEngine — orchestrates guardrail checks based on agent config.

Reads the guardrail_config JSON from the agent and runs the appropriate
checks on input (before model) and output (after model) text.
"""

import json
import logging
from typing import Dict, Any, Optional, List
from .base import GuardrailResult, GuardrailAction, GuardrailPhase, GuardrailCheckSummary
from .pii_detector import check_pii
from .prompt_injection import check_prompt_injection
from .content_policy import check_content_policy
from .output_limits import check_output_length
from .hallucination import check_hallucination

logger = logging.getLogger(__name__)

# Map guardrail type strings to checker functions
_CHECKERS = {
    "pii_detection": check_pii,
    "prompt_injection": check_prompt_injection,
    "content_policy": check_content_policy,
    "output_length": check_output_length,
    "hallucination_check": check_hallucination,
}

# Which guardrails apply to which phase by default
_DEFAULT_PHASES = {
    "pii_detection": {"input", "output"},
    "prompt_injection": {"input"},
    "content_policy": {"input", "output"},
    "output_length": {"output"},
    "hallucination_check": {"output"},
}


class GuardrailEngine:
    """
    Runs configured guardrails against text.

    Initialized from a guardrail_config dict (stored as JSON on agents_config).

    Example config:
    {
        "guardrails": [
            {
                "type": "pii_detection",
                "enabled": true,
                "action": "redact",
                "config": {
                    "detect_email": true,
                    "detect_phone": true,
                    "detect_ssn": true,
                    "detect_credit_card": true,
                    "detect_ip_address": true
                }
            },
            {
                "type": "prompt_injection",
                "enabled": true,
                "action": "block",
                "config": {"sensitivity": "medium"}
            }
        ]
    }
    """

    def __init__(self, guardrail_config: Optional[Dict[str, Any]] = None):
        self.config = guardrail_config or {}
        self.guardrails: List[Dict[str, Any]] = self.config.get("guardrails", [])

    @classmethod
    def from_json(cls, json_str: Optional[str]) -> "GuardrailEngine":
        """Create engine from JSON string (as stored in DB)."""
        if not json_str:
            return cls()
        try:
            return cls(json.loads(json_str))
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid guardrail_config JSON, using empty config")
            return cls()

    @property
    def has_guardrails(self) -> bool:
        return any(g.get("enabled", False) for g in self.guardrails)

    def check_input(self, text: str) -> GuardrailCheckSummary:
        """Run all input-phase guardrails."""
        return self._run_checks(text, GuardrailPhase.INPUT)

    def check_output(self, text: str) -> GuardrailCheckSummary:
        """Run all output-phase guardrails."""
        return self._run_checks(text, GuardrailPhase.OUTPUT)

    def _run_checks(self, text: str, phase: GuardrailPhase) -> GuardrailCheckSummary:
        summary = GuardrailCheckSummary()

        if not text or not self.guardrails:
            return summary

        for guardrail_def in self.guardrails:
            if not guardrail_def.get("enabled", False):
                continue

            g_type = guardrail_def.get("type", "")
            checker = _CHECKERS.get(g_type)
            if not checker:
                logger.warning(f"Unknown guardrail type: {g_type}")
                continue

            # Determine if this guardrail applies to the current phase
            allowed_phases = _DEFAULT_PHASES.get(g_type, {"input", "output"})
            apply_to = guardrail_def.get("apply_to")
            if apply_to:
                if apply_to == "input":
                    allowed_phases = {"input"}
                elif apply_to == "output":
                    allowed_phases = {"output"}
                elif apply_to == "both":
                    allowed_phases = {"input", "output"}

            if phase.value not in allowed_phases:
                continue

            action_str = guardrail_def.get("action", "log")
            try:
                action = GuardrailAction(action_str)
            except ValueError:
                action = GuardrailAction.LOG

            g_config = guardrail_def.get("config", {})

            try:
                result = checker(text, action, phase, g_config)
                summary.results.append(result)
            except Exception as e:
                logger.error(f"Error running guardrail {g_type}: {e}")
                summary.results.append(
                    GuardrailResult(
                        triggered=False,
                        guardrail_type=g_type,
                        action=action,
                        phase=phase,
                        details=f"Guardrail error: {e}",
                    )
                )

        return summary
