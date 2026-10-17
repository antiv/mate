"""
Reading the agent server's run request body the way the runtime will.

ADK's RunAgentRequest takes each field in snake_case or camelCase and, when a body
has both, uses the camelCase one; the LangGraph runtime uses the snake_case one.
Anything that checks the body before it is forwarded (the proxy's user check, the
rate limits) must therefore look at both spellings, or a body can show it one value
while the runtime uses the other.
"""

from typing import Any, Dict, Optional

_CAMEL = {"app_name": "appName", "user_id": "userId", "session_id": "sessionId"}


class ConflictingFieldError(ValueError):
    """The body gives a field two different values under its two spellings."""


def run_field(payload: Dict[str, Any], name: str) -> Optional[Any]:
    """The value of a run request field, refusing a body whose two spellings disagree."""
    snake = payload.get(name)
    camel = payload.get(_CAMEL[name])
    if snake is not None and camel is not None and snake != camel:
        raise ConflictingFieldError(f"{name} and {_CAMEL[name]} differ")
    return camel if camel is not None else snake
