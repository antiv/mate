"""
The key and endpoint a model call of a workspace uses (phase 4 of 2.0,
documents/design/2.0-plan.md).

One order, in model_credentials():

1. The agent's own key or base URL (model_api_key, model_base_url). create_model()
   receives them and leaves the call alone, so it never gets here.
2. The workspace's key for the model's provider (workspace_provider_keys), with its
   base URL or the provider's public one.
3. The environment, as 1.x does, when the workspace may use platform keys. The
   default workspace always may.
4. Otherwise the call fails with NoProviderKey.

"The environment" covers endpoints too (OLLAMA_API_BASE, OPENAI_BASE_URL, ...): a
workspace key is sent to the provider's public endpoint unless its row names one, and
a workspace that may not use platform keys reaches a local model only through its own
entry or the agent's base URL.

The key is looked up on every model call, not when the agent is built, so a saved,
changed or deleted key, and the workspace's "may use platform keys", apply to the next
call without reloading agents. with_provider_keys() wraps a LiteLLM client (ADK's
LiteLlm.llm_client, or ChatLiteLLM.client on LangGraph) to do that.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .models import DEFAULT_WORKSPACE_ID, Workspace, WorkspaceProviderKey
from .outbound import GuardedCompletions, _install_guard, private_network_allowed
from .utils import ModelContext, _detect_provider, _is_gemini_model

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    """A provider a workspace can hold a key for: the model prefixes it serves, and the
    public endpoint its key goes to when the workspace names none (LiteLLM's own
    default, given explicitly so that no *_API_BASE variable redirects the key)."""
    prefixes: tuple
    base_url: Optional[str]


PROVIDERS: Dict[str, Provider] = {
    # gemini-* and models/* on ADK's native backend too (provider_of)
    "google": Provider(("gemini",), "https://generativelanguage.googleapis.com/v1beta"),
    "openai": Provider(("openai",), "https://api.openai.com/v1"),
    "anthropic": Provider(("anthropic",), "https://api.anthropic.com"),
    "openrouter": Provider(("openrouter",), "https://openrouter.ai/api/v1"),
    "deepseek": Provider(("deepseek",), "https://api.deepseek.com/beta"),
    "mistral": Provider(("mistral",), "https://api.mistral.ai/v1"),
    "groq": Provider(("groq",), "https://api.groq.com/openai/v1"),
    "xai": Provider(("xai",), "https://api.x.ai/v1"),
    # A local server: the workspace's entry has to name it
    "ollama": Provider(("ollama", "ollama_chat"), None),
}


class NoProviderKey(RuntimeError):
    """A model call the workspace has no key for, and may not make on the platform's."""


@dataclass(frozen=True)
class Credentials:
    """What a call is sent with: the workspace's key, and where it goes. own_endpoint is
    True when the workspace's row named the base URL."""
    api_key: str
    base_url: Optional[str]
    workspace_id: int
    own_endpoint: bool


def provider_of(model_name: Optional[str]) -> str:
    """The provider a model name is served by: a name in PROVIDERS, or the LiteLLM
    prefix of one no workspace can hold a key for (lm_studio, together_ai, ...)."""
    name = (model_name or "").strip()
    if _is_gemini_model(name):
        return "google"
    prefix = _detect_provider(name)
    for provider, spec in PROVIDERS.items():
        if prefix in spec.prefixes:
            return provider
    return prefix


def _workspace_of(session: Any, context: ModelContext) -> Optional[int]:
    """As outbound.workspace_of_run, on the caller's session."""
    from .workspaces import agent_workspace, workspace_of_project
    if context.project_id is not None:
        return workspace_of_project(session, context.project_id)
    return agent_workspace(session, context.agent_name) or DEFAULT_WORKSPACE_ID


def model_credentials(context: ModelContext, model_name: Optional[str]) -> Optional[Credentials]:
    """The workspace's key for this model call, or None when the call uses the
    environment. Raises NoProviderKey when it may do neither.

    A model built for no agent (ModelContext()) is the installation's: None.
    """
    if not context.agent_name and context.project_id is None:
        return None
    provider = provider_of(model_name)
    from .database_client import get_database_client
    db = get_database_client()
    session = db.get_session() if db else None
    if session is None:
        raise NoProviderKey(f"No database to look up the model key for {context.describe()}")
    try:
        workspace_id = _workspace_of(session, context)
        if workspace_id is None:
            raise NoProviderKey(f"No workspace found for {context.describe()}")
        row = session.query(WorkspaceProviderKey).filter_by(
            workspace_id=workspace_id, provider=provider).first()
        if row is not None:
            if row.api_key is None:
                # Stored with another MATE_SECRET_KEY, or none: secret_box logged it
                raise NoProviderKey(f"This workspace's key for {provider} can't be read; enter it again")
            default = PROVIDERS[provider].base_url if provider in PROVIDERS else None
            if not (row.base_url or default):
                raise NoProviderKey(f"This workspace's {provider} entry has no base URL")
            return Credentials(api_key=row.api_key, base_url=row.base_url or default,
                               workspace_id=workspace_id, own_endpoint=bool(row.base_url))
        if workspace_id == DEFAULT_WORKSPACE_ID:
            return None
        allowed = session.query(Workspace.allow_platform_keys).filter_by(id=workspace_id).scalar()
        if allowed:
            return None
        raise NoProviderKey(f"This workspace has no key for {provider or 'this model'}")
    finally:
        session.close()


def _with_credentials(kwargs: Dict[str, Any], credentials: Credentials) -> Dict[str, Any]:
    """The call's arguments, sent with the workspace's key to its endpoint. What the
    model was built with from the environment (api_key, base_url) is replaced."""
    kwargs = dict(kwargs)
    kwargs.pop("base_url", None)
    kwargs["api_key"] = credentials.api_key
    kwargs["api_base"] = credentials.base_url
    return kwargs


class ProviderKeyCompletions:
    """A LiteLLM client (completion and acompletion) whose calls take the workspace's
    key, looked up on each call (model_credentials)."""

    def __init__(self, inner: Any, context: ModelContext, model_name: str):
        self._inner = inner
        self._context = context
        self._model_name = model_name

    def _client(self, credentials: Credentials) -> Any:
        # A base URL of the workspace's own, outside the default workspace: guarded as
        # an agent's own is (outbound.guard_model_client)
        if (credentials.own_endpoint and credentials.workspace_id != DEFAULT_WORKSPACE_ID
                and not private_network_allowed()):
            _install_guard()
            return GuardedCompletions(self._inner, credentials.workspace_id)
        return self._inner

    def completion(self, *args: Any, **kwargs: Any) -> Any:
        credentials = model_credentials(self._context, self._model_name)
        if credentials is None:
            return self._inner.completion(*args, **kwargs)
        return self._client(credentials).completion(*args, **_with_credentials(kwargs, credentials))

    async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
        credentials = await asyncio.to_thread(model_credentials, self._context, self._model_name)
        if credentials is None:
            return await self._inner.acompletion(*args, **kwargs)
        return await self._client(credentials).acompletion(*args, **_with_credentials(kwargs, credentials))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def with_provider_keys(model: Any, attribute: str, context: ModelContext, model_name: str) -> Any:
    """The model, its completion client (`attribute`) taking the workspace's key on
    each call. Left as it is for a model built for no agent."""
    if not context.agent_name and context.project_id is None:
        return model
    object.__setattr__(model, attribute,
                       ProviderKeyCompletions(getattr(model, attribute), context, model_name))
    return model
