"""
Who may use the MCP servers MATE serves at /agents/<name>/mcp and /gdrive/mcp.

The same callers as the OpenAI-compatible API: a Personal Access Token, the
dashboard's login token, Basic auth or a dashboard session, held by an admin or
by a role in ALLOWED_API_ROLES. An agent's MCP server also checks that the agent
is in the request's workspace (a token's own, server/pat_auth.py) and the agent's
own allowed_for_roles: the tool call reaches the agent over A2A without the
caller's identity, so nothing further along can check it. The Google Drive server
works with the installation's service account, which is the default workspace's
(shared/utils/platform_only.py): a request of another workspace needs a platform
administrator.
"""

import json
import logging

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


async def require_mcp_user(request: Request) -> str:
    """FastAPI dependency: the caller's user_id, or 401/403 from get_pat_user."""
    from server.pat_auth import get_pat_user
    user = await get_pat_user(request)
    return user.user_id


async def require_drive_user(request: Request) -> str:
    """FastAPI dependency for /gdrive/mcp: require_mcp_user, in the default workspace
    unless the caller is a platform administrator; 403 otherwise."""
    from server.pat_auth import get_pat_user, is_platform_admin
    from ..models import DEFAULT_WORKSPACE_ID
    user = await get_pat_user(request)
    if request.state.workspace_id != DEFAULT_WORKSPACE_ID and not is_platform_admin(user):
        raise HTTPException(status_code=403,
                            detail="Google Drive uses the installation's account: default workspace only")
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
