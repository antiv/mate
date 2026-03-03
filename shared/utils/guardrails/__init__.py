"""
Guardrail engine for MATE agents.

Provides configurable input/output guardrails:
- PII detection (regex-based)
- Prompt injection detection
- Content policy enforcement (blocklist, regex)
- Output length limits
- Hallucination check (optional LLM-as-judge)

Each guardrail supports actions: block, warn, log, redact.
"""

from .base import GuardrailResult, GuardrailAction
from .engine import GuardrailEngine

__all__ = ["GuardrailEngine", "GuardrailResult", "GuardrailAction"]
