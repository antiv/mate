"""
Requests the server makes to a URL a workspace configured: an alert rule's or a
trigger's webhook, an agent's MCP server or model endpoint.

The server makes them from inside, where the agent server (port 8001, no
authentication), the database and cloud metadata are reachable. An installation's
own configuration may point there (a local Ollama, an MCP server next to MATE), so
the default workspace is left as it is. A URL of another workspace that resolves to
a private, loopback, link-local or reserved address is refused, unless
OUTBOUND_ALLOW_PRIVATE_NETWORK=true says otherwise.

The host is resolved when the check runs, not when the request is made, so a name
whose address changes in between is not caught (DNS rebinding).
"""

import ipaddress
import logging
import os
import socket
from typing import Optional
from urllib.parse import urlparse

from .models import DEFAULT_WORKSPACE_ID

logger = logging.getLogger(__name__)


def private_network_allowed() -> bool:
    """True when OUTBOUND_ALLOW_PRIVATE_NETWORK lets every workspace reach private addresses."""
    return os.getenv("OUTBOUND_ALLOW_PRIVATE_NETWORK", "false").lower() in ("true", "1", "yes")


def _is_internal(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
            or addr.is_multicast or addr.is_unspecified)


def internal_address(url: str) -> Optional[str]:
    """Why the URL reaches the server's own network, or None when it doesn't. A host
    that doesn't resolve is None: the request fails on its own."""
    host = urlparse(url).hostname
    if not host:
        return "no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return None
    for info in infos:
        if _is_internal(info[4][0]):
            return f"{host} resolves to an internal address"
    return None


def _workspace_of(agent_name: Optional[str], project_id: Optional[int]) -> Optional[int]:
    """The workspace of the project, else of the agent; the default one for an agent
    with no config. None when the database can't say, which the caller refuses."""
    from .database_client import get_database_client
    from .workspaces import agent_workspace, workspace_of_project
    try:
        session = get_database_client().get_session()
        if not session:
            return None
        try:
            if project_id is not None:
                return workspace_of_project(session, project_id)
            return (agent_workspace(session, agent_name) if agent_name else None) or DEFAULT_WORKSPACE_ID
        finally:
            session.close()
    except Exception as e:
        logger.error("Outbound check: no workspace for %s / project %s: %s", agent_name, project_id, e)
        return None


def outbound_refusal(url: str, *, workspace_id: Optional[int] = None,
                     agent_name: Optional[str] = None,
                     project_id: Optional[int] = None) -> Optional[str]:
    """Why the server may not send to this URL for that workspace (given, or looked up
    from the project or agent), or None when it may."""
    if private_network_allowed():
        return None
    if workspace_id is None:
        workspace_id = _workspace_of(agent_name, project_id)
    if workspace_id == DEFAULT_WORKSPACE_ID:
        return None
    reason = internal_address(url)
    if reason:
        logger.warning("Outbound request to %s refused for workspace %s: %s",
                       url[:200], workspace_id, reason)
    return reason
