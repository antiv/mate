"""
Hallucination detection guardrail (optional LLM-as-judge).

This is a stub that can be extended with an actual LLM call to judge
whether the output is grounded in the provided context. When enabled,
it uses a secondary LLM call to evaluate the response.

Currently returns not-triggered by default unless explicitly wired up
with a model endpoint. The check is intentionally lightweight so it
can be enabled per-agent without significant latency impact.
"""

import logging
from typing import Dict, Any
from .base import GuardrailResult, GuardrailAction, GuardrailPhase

logger = logging.getLogger(__name__)


def check_hallucination(
    text: str,
    action: GuardrailAction,
    phase: GuardrailPhase,
    config: Dict[str, Any],
) -> GuardrailResult:
    """
    Placeholder for hallucination detection.

    To implement:
    1. Extract context/grounding sources from the conversation
    2. Call a judge LLM with: "Is this response grounded in the given context?"
    3. Parse the judge's score against config['threshold']

    Config keys:
        model: str — model name for the judge (e.g. "gemini-2.0-flash")
        threshold: float — score threshold (0.0-1.0), default 0.7
    """
    logger.debug(
        "Hallucination check called (stub) — model=%s, threshold=%s",
        config.get("model", "not configured"),
        config.get("threshold", 0.7),
    )

    return GuardrailResult(
        triggered=False,
        guardrail_type="hallucination_check",
        action=action,
        phase=phase,
        details="Hallucination check stub — enable LLM-as-judge for full support",
    )
