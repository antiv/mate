"""
Who may use the MCP servers MATE serves at /agents/<name>/mcp and /gdrive/mcp.

The same callers as the OpenAI-compatible API: a Personal Access Token, the
dashboard's login token, Basic auth or a dashboard session, held by an admin or
by a role in ALLOWED_API_ROLES. An agent's MCP server also checks the agent's
own allowed_for_roles: the tool call reaches the agent over A2A without the
caller's identity, so nothing further along can check it.
"""

import json
import logging

from fastapi import Request

logger = logging.getLogger(__name__)


async def require_mcp_user(request: Request) -> str:
    """FastAPI dependency: the caller's user_id, or 401/403 from get_pat_user."""
    from server.pat_auth import get_pat_user
    user = await get_pat_user(request)
    return user.user_id


def agent_allowed(user_id: str, agent_name: str) -> bool:
    """True if the user's roles grant access to the agent (admin-only when it has none)."""
    from ..database_client import get_database_client
    from ..models import AgentConfig
    from ..rbac_middleware import get_rbac_middleware

    session = get_database_client().get_session()
    if not session:
        return False
    try:
        config = session.query(AgentConfig).filter_by(name=agent_name).first()
        raw_roles = config.allowed_for_roles if config else None
    finally:
        session.close()
    try:
        roles = json.loads(raw_roles) if raw_roles else []
    except (TypeError, ValueError):
        logger.warning("Invalid JSON in allowed_for_roles for agent %s", agent_name)
        roles = []
    allowed, _ = get_rbac_middleware().check_agent_access(
        user_id, {"name": agent_name, "allowed_for_roles": roles})
    return allowed
