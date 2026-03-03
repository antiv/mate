"""
PII detection guardrail using regex patterns.

Detects: email addresses, phone numbers, SSNs, credit card numbers, IP addresses.
Supports custom patterns via config.
"""

import re
from typing import Dict, Any, List, Tuple
from .base import GuardrailResult, GuardrailAction, GuardrailPhase

# Pre-compiled PII patterns
PII_PATTERNS: Dict[str, re.Pattern] = {
    "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    "phone": re.compile(r'(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
    "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    "credit_card": re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
    "ip_address": re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
}

REDACTION_PLACEHOLDER = {
    "email": "[EMAIL_REDACTED]",
    "phone": "[PHONE_REDACTED]",
    "ssn": "[SSN_REDACTED]",
    "credit_card": "[CC_REDACTED]",
    "ip_address": "[IP_REDACTED]",
}


def _get_enabled_patterns(config: Dict[str, Any]) -> Dict[str, re.Pattern]:
    """Build pattern dict based on config toggles."""
    enabled = {}
    for pii_type, pattern in PII_PATTERNS.items():
        config_key = f"detect_{pii_type}"
        if config.get(config_key, True):
            enabled[pii_type] = pattern

    for custom in config.get("custom_patterns", []):
        try:
            name = custom.get("name", f"custom_{len(enabled)}")
            enabled[name] = re.compile(custom["pattern"])
        except (re.error, KeyError):
            continue

    return enabled


def _find_matches(text: str, patterns: Dict[str, re.Pattern]) -> List[Tuple[str, str]]:
    """Return list of (pii_type, matched_text) tuples."""
    matches = []
    for pii_type, pattern in patterns.items():
        for m in pattern.finditer(text):
            matches.append((pii_type, m.group()))
    return matches


def _redact(text: str, patterns: Dict[str, re.Pattern]) -> str:
    """Replace all PII matches with redaction placeholders."""
    result = text
    for pii_type, pattern in patterns.items():
        placeholder = REDACTION_PLACEHOLDER.get(pii_type, f"[{pii_type.upper()}_REDACTED]")
        result = pattern.sub(placeholder, result)
    return result


def check_pii(
    text: str,
    action: GuardrailAction,
    phase: GuardrailPhase,
    config: Dict[str, Any],
) -> GuardrailResult:
    """Run PII detection on text."""
    patterns = _get_enabled_patterns(config)
    matches = _find_matches(text, patterns)

    if not matches:
        return GuardrailResult(
            triggered=False,
            guardrail_type="pii_detection",
            action=action,
            phase=phase,
        )

    types_found = sorted(set(t for t, _ in matches))
    sample = "; ".join(f"{t}: {v[:20]}..." if len(v) > 20 else f"{t}: {v}" for t, v in matches[:5])

    redacted_text = None
    if action == GuardrailAction.REDACT:
        redacted_text = _redact(text, patterns)

    return GuardrailResult(
        triggered=True,
        guardrail_type="pii_detection",
        action=action,
        phase=phase,
        matched_content=sample,
        details=f"PII detected: {', '.join(types_found)} ({len(matches)} occurrence(s))",
        redacted_text=redacted_text,
    )
