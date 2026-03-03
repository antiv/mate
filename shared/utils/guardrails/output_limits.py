"""
Output length limit guardrail.

Enforces maximum character count and/or word count on agent output.
"""

from typing import Dict, Any
from .base import GuardrailResult, GuardrailAction, GuardrailPhase


def check_output_length(
    text: str,
    action: GuardrailAction,
    phase: GuardrailPhase,
    config: Dict[str, Any],
) -> GuardrailResult:
    """Check if output exceeds configured length limits."""
    max_characters = config.get("max_characters")
    max_words = config.get("max_words")

    violations = []
    char_count = len(text)
    word_count = len(text.split())

    if max_characters and char_count > max_characters:
        violations.append(f"characters: {char_count}/{max_characters}")

    if max_words and word_count > max_words:
        violations.append(f"words: {word_count}/{max_words}")

    if not violations:
        return GuardrailResult(
            triggered=False,
            guardrail_type="output_length",
            action=action,
            phase=phase,
        )

    redacted_text = None
    if action == GuardrailAction.REDACT:
        if max_characters and char_count > max_characters:
            redacted_text = text[:max_characters] + "\n\n[OUTPUT TRUNCATED — exceeded limit]"
        elif max_words and word_count > max_words:
            words = text.split()
            redacted_text = " ".join(words[:max_words]) + "\n\n[OUTPUT TRUNCATED — exceeded limit]"

    return GuardrailResult(
        triggered=True,
        guardrail_type="output_length",
        action=action,
        phase=phase,
        matched_content=f"length: {char_count} chars, {word_count} words",
        details=f"Output length exceeded: {', '.join(violations)}",
        redacted_text=redacted_text,
    )
