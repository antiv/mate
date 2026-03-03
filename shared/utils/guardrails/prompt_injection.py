"""
Prompt injection detection guardrail.

Uses pattern matching with configurable sensitivity levels to detect
common prompt injection techniques: role hijacking, instruction override,
delimiter manipulation, etc.
"""

import re
from typing import Dict, Any, List
from .base import GuardrailResult, GuardrailAction, GuardrailPhase

# Patterns ordered by severity; higher-sensitivity configs match more patterns
_INJECTION_PATTERNS_HIGH: List[re.Pattern] = [
    re.compile(r'ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|directives?)', re.I),
    re.compile(r'disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)', re.I),
    re.compile(r'forget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|context|rules?)', re.I),
    re.compile(r'you\s+are\s+now\s+(?:a|an|the)\s+', re.I),
    re.compile(r'act\s+as\s+(?:a|an|the|if\s+you\s+(?:are|were))\s+', re.I),
    re.compile(r'pretend\s+(?:you\s+are|to\s+be)\s+', re.I),
    re.compile(r'from\s+now\s+on[,\s]+(?:you|your)\s+', re.I),
    re.compile(r'new\s+instruction[s]?\s*:', re.I),
    re.compile(r'override\s+(system|safety|all)\s+(instructions?|prompt|settings?)', re.I),
    re.compile(r'system\s*prompt\s*:', re.I),
    re.compile(r'\[system\]', re.I),
    re.compile(r'<\|?(?:system|im_start|im_end)\|?>', re.I),
    re.compile(r'---+\s*(?:BEGIN|START)\s+(?:SYSTEM|NEW)\s+', re.I),
    re.compile(r'reveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?|rules?)', re.I),
    re.compile(r'(?:what|show|print|display|output)\s+(?:is|are)\s+your\s+(?:system\s+)?(?:prompt|instructions?|rules?)', re.I),
    re.compile(r'do\s+not\s+follow\s+(?:your|the|any)\s+(?:rules?|instructions?|guidelines?)', re.I),
    re.compile(r'jailbreak', re.I),
    re.compile(r'DAN\s+mode', re.I),
]

_INJECTION_PATTERNS_MEDIUM: List[re.Pattern] = _INJECTION_PATTERNS_HIGH[:12]
_INJECTION_PATTERNS_LOW: List[re.Pattern] = _INJECTION_PATTERNS_HIGH[:6]

_SENSITIVITY_MAP = {
    "high": _INJECTION_PATTERNS_HIGH,
    "medium": _INJECTION_PATTERNS_MEDIUM,
    "low": _INJECTION_PATTERNS_LOW,
}


def check_prompt_injection(
    text: str,
    action: GuardrailAction,
    phase: GuardrailPhase,
    config: Dict[str, Any],
) -> GuardrailResult:
    """Check text for prompt injection patterns."""
    sensitivity = config.get("sensitivity", "medium")
    patterns = _SENSITIVITY_MAP.get(sensitivity, _INJECTION_PATTERNS_MEDIUM)

    matched = []
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            matched.append(m.group())

    if not matched:
        return GuardrailResult(
            triggered=False,
            guardrail_type="prompt_injection",
            action=action,
            phase=phase,
        )

    sample = "; ".join(matched[:3])
    return GuardrailResult(
        triggered=True,
        guardrail_type="prompt_injection",
        action=action,
        phase=phase,
        matched_content=sample[:200],
        details=f"Prompt injection detected ({len(matched)} pattern(s) matched, sensitivity={sensitivity})",
    )
