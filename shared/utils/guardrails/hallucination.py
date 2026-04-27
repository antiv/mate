"""
Hallucination detection guardrail — LLM-as-judge implementation.

Uses litellm.completion() directly (not ADK) to score the response for
factual consistency and grounding. Fails open on any model error so that
a misconfigured judge never silently blocks valid responses.

Config keys:
    model:     litellm model string, e.g. "gemini/gemini-2.0-flash"
    threshold: float 0.0-1.0 — score below which the guardrail triggers (default 0.7)
"""

import json
import logging
from typing import Any, Dict

from .base import GuardrailAction, GuardrailPhase, GuardrailResult

logger = logging.getLogger(__name__)

_PROMPT = """\
You are a fact-checking evaluator. Assess whether the following AI response:
1. Is factually consistent (does not contradict established facts)
2. Does not fabricate specific claims, names, numbers, or citations

Response to evaluate:
{text}

Reply ONLY with a JSON object on a single line:
{{"score": <float 0.0-1.0>, "reasoning": "<one sentence>"}}

Where 1.0 = fully grounded and factually consistent, 0.0 = contains clear hallucinations.\
"""


def _parse_judge_response(content: str) -> tuple[float, str]:
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("```")
        content = parts[1] if len(parts) > 1 else content
        if content.startswith("json"):
            content = content[4:]
    parsed = json.loads(content.strip())
    score = max(0.0, min(1.0, float(parsed["score"])))
    return score, parsed.get("reasoning", "")


def check_hallucination(
    text: str,
    action: GuardrailAction,
    phase: GuardrailPhase,
    config: Dict[str, Any],
) -> GuardrailResult:
    model = config.get("model")
    threshold = float(config.get("threshold", 0.7))

    if not model:
        logger.debug("Hallucination check skipped — no model configured")
        return GuardrailResult(
            triggered=False,
            guardrail_type="hallucination_check",
            action=action,
            phase=phase,
            details="Hallucination check skipped: no model configured",
        )

    try:
        import litellm  # type: ignore

        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
            temperature=0.0,
            max_tokens=200,
        )
        score, reasoning = _parse_judge_response(response.choices[0].message.content)
        triggered = score < threshold
        logger.debug(
            "Hallucination check: model=%s score=%.3f threshold=%.2f triggered=%s",
            model, score, threshold, triggered,
        )
        return GuardrailResult(
            triggered=triggered,
            guardrail_type="hallucination_check",
            action=action,
            phase=phase,
            details=f"score={score:.3f} threshold={threshold} reasoning={reasoning}",
        )
    except Exception as e:
        logger.warning("Hallucination check failed (fail-open): %s", e)
        return GuardrailResult(
            triggered=False,
            guardrail_type="hallucination_check",
            action=action,
            phase=phase,
            details=f"Hallucination check error (fail-open): {e}",
        )
