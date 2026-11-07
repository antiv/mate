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

A model endpoint is called by LiteLLM, whose HTTP clients follow redirects: a public
base URL could answer 307 to an internal one. guard_model_client() wraps the model's
completion calls so that, while one runs, every httpx request is checked the same way
and no redirect is followed (_install_guard).
"""

import asyncio
import ipaddress
import logging
import os
import socket
from contextvars import ContextVar
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .models import DEFAULT_WORKSPACE_ID

logger = logging.getLogger(__name__)


def private_network_allowed() -> bool:
    """True when OUTBOUND_ALLOW_PRIVATE_NETWORK lets every workspace reach private addresses."""
    return os.getenv("OUTBOUND_ALLOW_PRIVATE_NETWORK", "false").lower() in ("true", "1", "yes")


def is_internal_address(ip: str) -> bool:
    """True for a private, loopback, link-local, reserved, multicast or unspecified
    address: the server's own network. Also used by the browser and image tools."""
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
        if is_internal_address(info[4][0]):
            return f"{host} resolves to an internal address"
    return None


def workspace_of_run(agent_name: Optional[str], project_id: Optional[int]) -> Optional[int]:
    """The workspace an agent runs in: its project's, else its own; the default one for
    an agent with no config. None when the database can't say, or the project doesn't
    exist, which every caller refuses. Also platform_only.may_run_code's rule."""
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
        logger.error("No workspace for %s / project %s: %s", agent_name, project_id, e)
        return None


def outbound_refusal(url: str, *, workspace_id: Optional[int] = None,
                     agent_name: Optional[str] = None,
                     project_id: Optional[int] = None) -> Optional[str]:
    """Why the server may not send to this URL for that workspace (given, or looked up
    from the project or agent), or None when it may."""
    if private_network_allowed():
        return None
    if workspace_id is None:
        workspace_id = workspace_of_run(agent_name, project_id)
    if workspace_id == DEFAULT_WORKSPACE_ID:
        return None
    reason = internal_address(url)
    if reason:
        logger.warning("Outbound request to %s refused for workspace %s: %s",
                       url[:200], workspace_id, reason)
    return reason


# The workspace whose model call is running in this context, when it is guarded
_GUARDED: ContextVar[Optional[int]] = ContextVar("mate_outbound_guarded", default=None)
_guard_installed = False


def _install_guard() -> None:
    """Make httpx's send() refuse an internal address and follow no redirect while a
    guarded model call runs. Every request of LiteLLM and the OpenAI SDK, and each
    redirect LiteLLM follows by hand, goes through it. Other requests are untouched."""
    global _guard_installed
    if _guard_installed:
        return
    _guard_installed = True
    sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send

    def _refused(request: httpx.Request, reason: str) -> httpx.ConnectError:
        logger.warning("Model request to %s refused for workspace %s: %s",
                       str(request.url)[:200], _GUARDED.get(), reason)
        return httpx.ConnectError(f"model endpoint refused: {reason}", request=request)

    def send(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if _GUARDED.get() is not None:
            reason = internal_address(str(request.url))
            if reason:
                raise _refused(request, reason)
            kwargs["follow_redirects"] = False
        return sync_send(self, request, **kwargs)

    async def asend(self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if _GUARDED.get() is not None:
            reason = await asyncio.to_thread(internal_address, str(request.url))
            if reason:
                raise _refused(request, reason)
            kwargs["follow_redirects"] = False
        return await async_send(self, request, **kwargs)

    httpx.Client.send = send
    httpx.AsyncClient.send = asend


class _GuardedStream:
    """A streamed answer whose chunks are read guarded too: some providers (gemini and
    vertex_ai through LiteLLM, codestral, predibase) send the request only when the
    first chunk is read, after acompletion() has returned."""

    def __init__(self, inner: Any, workspace_id: int):
        self._inner = inner
        self._workspace_id = workspace_id

    def __aiter__(self) -> "_GuardedStream":
        return self

    async def __anext__(self) -> Any:
        token = _GUARDED.set(self._workspace_id)
        try:
            return await self._inner.__anext__()
        finally:
            _GUARDED.reset(token)

    def __iter__(self) -> "_GuardedStream":
        return self

    def __next__(self) -> Any:
        token = _GUARDED.set(self._workspace_id)
        try:
            return next(self._inner)
        finally:
            _GUARDED.reset(token)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _guarded_stream(result: Any, workspace_id: int) -> Any:
    """The result, its chunks read guarded when it is a stream."""
    if hasattr(result, "__anext__") or hasattr(result, "__next__"):
        return _GuardedStream(result, workspace_id)
    return result


class GuardedCompletions:
    """An object with completion() and acompletion() (ADK's LiteLLMClient, or the
    litellm module ChatLiteLLM calls), whose calls run guarded."""

    def __init__(self, inner: Any, workspace_id: int):
        self._inner = inner
        self._workspace_id = workspace_id

    def completion(self, *args: Any, **kwargs: Any) -> Any:
        token = _GUARDED.set(self._workspace_id)
        try:
            return _guarded_stream(self._inner.completion(*args, **kwargs), self._workspace_id)
        finally:
            _GUARDED.reset(token)

    async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
        token = _GUARDED.set(self._workspace_id)
        try:
            return _guarded_stream(await self._inner.acompletion(*args, **kwargs), self._workspace_id)
        finally:
            _GUARDED.reset(token)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def guard_model_client(model: Any, attribute: str, base_url: Optional[str],
                       agent_name: Optional[str], project_id: Optional[int]) -> Any:
    """The model, with its completion client (`attribute`) guarded when it is sent to
    a base URL of an agent outside the default workspace. A provider's own endpoint,
    with no base URL configured, is the installation's and stays as it is."""
    if not base_url or private_network_allowed() or (not agent_name and project_id is None):
        return model
    workspace_id = workspace_of_run(agent_name, project_id)
    if workspace_id == DEFAULT_WORKSPACE_ID:
        return model
    _install_guard()
    # 0 when the database couldn't say: guarded all the same
    guarded = GuardedCompletions(getattr(model, attribute), workspace_id or 0)
    object.__setattr__(model, attribute, guarded)
    return model
