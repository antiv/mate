"""
Content policy guardrail.

Enforces blocklist words and custom regex patterns.
"""

import re
from typing import Dict, Any, List
from .base import GuardrailResult, GuardrailAction, GuardrailPhase


def check_content_policy(
    text: str,
    action: GuardrailAction,
    phase: GuardrailPhase,
    config: Dict[str, Any],
) -> GuardrailResult:
    """Check text against content policy blocklist and regex patterns."""
    case_sensitive = config.get("case_sensitive", False)
    compare_text = text if case_sensitive else text.lower()

    violations: List[str] = []

    # Blocklist check
    blocklist = config.get("blocklist", [])
    for word in blocklist:
        check_word = word if case_sensitive else word.lower()
        if check_word in compare_text:
            violations.append(f"blocklist: '{word}'")

    # Regex patterns check
    regex_patterns = config.get("regex_patterns", [])
    flags = 0 if case_sensitive else re.IGNORECASE
    for pattern_str in regex_patterns:
        try:
            pattern = re.compile(pattern_str, flags)
            m = pattern.search(text)
            if m:
                violations.append(f"pattern: '{pattern_str}' -> '{m.group()[:50]}'")
        except re.error:
            continue

    if not violations:
        return GuardrailResult(
            triggered=False,
            guardrail_type="content_policy",
            action=action,
            phase=phase,
        )

    redacted_text = None
    if action == GuardrailAction.REDACT:
        redacted_text = text
        for word in blocklist:
            flags_re = 0 if case_sensitive else re.IGNORECASE
            redacted_text = re.sub(re.escape(word), "[REDACTED]", redacted_text, flags=flags_re)
        for pattern_str in regex_patterns:
            try:
                redacted_text = re.sub(pattern_str, "[REDACTED]", redacted_text, flags=flags)
            except re.error:
                continue

    return GuardrailResult(
        triggered=True,
        guardrail_type="content_policy",
        action=action,
        phase=phase,
        matched_content="; ".join(violations[:5]),
        details=f"Content policy violation: {len(violations)} match(es)",
        redacted_text=redacted_text,
    )
