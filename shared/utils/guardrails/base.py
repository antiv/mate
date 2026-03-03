"""
Base classes for the guardrail system.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class GuardrailAction(str, Enum):
    BLOCK = "block"
    WARN = "warn"
    LOG = "log"
    REDACT = "redact"


class GuardrailPhase(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass
class GuardrailResult:
    """Result from a single guardrail check."""
    triggered: bool
    guardrail_type: str
    action: GuardrailAction
    phase: GuardrailPhase
    matched_content: Optional[str] = None
    details: Optional[str] = None
    redacted_text: Optional[str] = None


@dataclass
class GuardrailCheckSummary:
    """Aggregated results from all guardrail checks on a single message."""
    results: List[GuardrailResult] = field(default_factory=list)

    @property
    def should_block(self) -> bool:
        return any(r.triggered and r.action == GuardrailAction.BLOCK for r in self.results)

    @property
    def has_warnings(self) -> bool:
        return any(r.triggered and r.action == GuardrailAction.WARN for r in self.results)

    @property
    def triggered_results(self) -> List[GuardrailResult]:
        return [r for r in self.results if r.triggered]

    @property
    def block_reasons(self) -> List[str]:
        return [
            r.details or f"Blocked by {r.guardrail_type}"
            for r in self.results
            if r.triggered and r.action == GuardrailAction.BLOCK
        ]
