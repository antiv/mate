"""
Who may use the MCP servers MATE serves at /agents/<name>/mcp and /gdrive/mcp.

The same callers as the OpenAI-compatible API: a Personal Access Token, the
dashboard's login token, Basic auth or a dashboard session, held by an admin or
by a role in ALLOWED_API_ROLES. An agent's MCP server also checks that the agent
is in the request's workspace (a token's own, server/pat_auth.py) and the agent's
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


def mcp_workspace(request: Request) -> int:
    """The workspace require_mcp_user found for the request."""
    from server.pat_auth import pat_workspace
    return pat_workspace(request)


def agent_allowed(user_id: str, agent_name: str, workspace_id: int) -> bool:
    """True if the agent is in the workspace and the user's roles grant access to it
    (admin-only when it has none)."""
    from ..database_client import get_database_client
    from ..models import AgentConfig
    from ..rbac_middleware import get_rbac_middleware
    from ..workspaces import agent_workspace

    session = get_database_client().get_session()
    if not session:
        return False
    try:
        if agent_workspace(session, agent_name) != workspace_id:
            return False
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
