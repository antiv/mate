"""
AI disclosure (EU AI Act Art. 50).

Art. 50(1) requires that a person interacting with an AI system is informed of
that fact, unless it is obvious to a reasonably well-informed person. The
obligation has applied since 2 August 2026.

MATE cannot make anyone compliant — the obligation sits with the provider or
deployer of the system. What it can do is make the disclosure the default on the
surfaces where it is least obvious (an embedded widget on someone else's site,
styled to match it) and make turning it off leave a record.

The model enforces that: an agent has no boolean for this. Disclosure is on
unless `ai_disclosure_waiver` holds a reason, so the reason and the decision are
the same field and cannot come apart.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_DISCLOSURE = "You are chatting with an AI assistant."

# The waiver is meant to carry a justification a regulator could read, not "n/a".
MIN_WAIVER_REASON_CHARS = 10


def resolve_disclosure(config: Any) -> Optional[str]:
    """
    The disclosure text to show for an agent, or None if it has been waived.

    Accepts an AgentConfig row or a plain dict, since agent configuration reaches
    different parts of MATE in both forms.
    """
    if isinstance(config, dict):
        waiver = config.get("ai_disclosure_waiver")
        text = config.get("ai_disclosure")
    else:
        waiver = getattr(config, "ai_disclosure_waiver", None)
        text = getattr(config, "ai_disclosure", None)

    if waiver and waiver.strip():
        return None

    text = (text or "").strip()
    return text or DEFAULT_DISCLOSURE


def validate_waiver(waiver: Optional[str]) -> Optional[str]:
    """
    Check a waiver before it is stored. Returns an error message, or None if fine.

    An empty waiver is simply "no waiver" and is allowed — that is the default
    state. What is refused is a waiver too thin to be a justification, because a
    disclosure switched off against the reason "x" is switched off silently in
    every way that matters.
    """
    if waiver is None:
        return None
    waiver = waiver.strip()
    if not waiver:
        return None
    if len(waiver) < MIN_WAIVER_REASON_CHARS:
        return (
            f"A disclosure waiver needs a reason of at least "
            f"{MIN_WAIVER_REASON_CHARS} characters, recording why this agent does "
            f"not have to tell people they are talking to an AI."
        )
    return None


def disclosure_state(config: Any) -> Dict[str, Any]:
    """The agent's disclosure as it appears in a compliance record."""
    text = resolve_disclosure(config)
    if isinstance(config, dict):
        waiver = config.get("ai_disclosure_waiver")
    else:
        waiver = getattr(config, "ai_disclosure_waiver", None)
    return {
        "shown": text is not None,
        "text": text,
        "waiver_reason": (waiver or "").strip() or None,
    }
