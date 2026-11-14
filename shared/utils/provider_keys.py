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
call without reloading agents:

- with_provider_keys() wraps a LiteLLM client (ADK's LiteLlm.llm_client, or
  ChatLiteLLM.client on LangGraph);
- WorkspaceGemini is ADK's native Gemini backend (gemini-* models);
- google_client_kwargs() builds google.genai's client for File Search;
- completion() and litellm_credentials() serve the calls that go to litellm
  directly: the image tool, embeddings, the eval judge, the improver, Work Room
  titles, the hallucination guardrail and the mystery game.
"""

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from google.adk.models.google_llm import Gemini
from pydantic import PrivateAttr

from .models import DEFAULT_WORKSPACE_ID, Workspace, WorkspaceProviderKey
from .outbound import _GUARDED, GuardedCompletions, _install_guard, private_network_allowed
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


def litellm_provider_of(model_name: Optional[str]) -> str:
    """provider_of() for a name LiteLLM is called with directly: there a bare name is
    LiteLLM's to place (dall-e-3 and text-embedding-3-small are OpenAI's), where
    create_model() sends it to Gemini's own backend."""
    name = (model_name or "").strip()
    if "/" in name:
        return provider_of(name)
    try:
        import litellm
        prefix = litellm.get_llm_provider(name)[1]
    except Exception:
        return ""
    return provider_of(f"{prefix}/{name}")


def workspace_of_context(context: ModelContext) -> Optional[int]:
    """The workspace of an agent or project; None for the installation, or when the
    database can't say."""
    if not context.agent_name and context.project_id is None:
        return None
    from .database_client import get_database_client
    db = get_database_client()
    session = db.get_session() if db else None
    if session is None:
        return None
    try:
        return _workspace_of(session, context)
    finally:
        session.close()


def model_credentials(context: ModelContext, model_name: Optional[str],
                      provider: Optional[str] = None) -> Optional[Credentials]:
    """The workspace's key for this model call, or None when the call uses the
    environment. Raises NoProviderKey when it may do neither. provider, when given,
    is used instead of provider_of(model_name).

    A model built for no agent (ModelContext()) is the installation's: None.
    """
    if not context.agent_name and context.project_id is None:
        return None
    provider = provider if provider is not None else provider_of(model_name)
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


def masked(api_key: str) -> str:
    """A key as the dashboard shows it: its last four characters."""
    return "••••" + api_key[-4:] if len(api_key) > 8 else "••••"


def redact(error: BaseException, api_key: Optional[str]) -> BaseException:
    """The error, with the key masked in it and in the errors it was raised from. Some
    providers repeat the key they were sent in their error, and the error reaches logs
    (ADK logs a failed call's traceback) and responses. Changed in place, so its type,
    and what the fallback model decides from it, stay."""
    if not api_key or len(api_key) < 8:
        return error
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            current.args = tuple(a.replace(api_key, masked(api_key)) if isinstance(a, str) else a
                                 for a in current.args)
            for name, value in list(vars(current).items()):
                if isinstance(value, str) and api_key in value:
                    setattr(current, name, value.replace(api_key, masked(api_key)))
        except Exception:
            pass
        current = current.__cause__ or current.__context__
    return error


class _RedactingStream:
    """A streamed answer whose errors, raised while it is read, are redacted too."""

    def __init__(self, inner: Any, api_key: str):
        self._inner = inner
        self._api_key = api_key

    def __aiter__(self) -> "_RedactingStream":
        return self

    async def __anext__(self) -> Any:
        try:
            return await self._inner.__anext__()
        except StopAsyncIteration:
            raise
        except Exception as e:
            raise redact(e, self._api_key)

    def __iter__(self) -> "_RedactingStream":
        return self

    def __next__(self) -> Any:
        try:
            return next(self._inner)
        except StopIteration:
            raise
        except Exception as e:
            raise redact(e, self._api_key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _redacting(result: Any, api_key: str) -> Any:
    if hasattr(result, "__anext__") or hasattr(result, "__next__"):
        return _RedactingStream(result, api_key)
    return result


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
        try:
            return _redacting(self._client(credentials).completion(
                *args, **_with_credentials(kwargs, credentials)), credentials.api_key)
        except Exception as e:
            raise redact(e, credentials.api_key)

    async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
        credentials = await asyncio.to_thread(model_credentials, self._context, self._model_name)
        if credentials is None:
            return await self._inner.acompletion(*args, **kwargs)
        try:
            return _redacting(await self._client(credentials).acompletion(
                *args, **_with_credentials(kwargs, credentials)), credentials.api_key)
        except Exception as e:
            raise redact(e, credentials.api_key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def litellm_credentials(context: ModelContext, model_name: str) -> Tuple[Dict[str, Any], Optional[int]]:
    """For a call to litellm made directly (embedding, image, judge, ...): the
    arguments it adds ({} where the environment applies), and the workspace to guard it
    for when they name the workspace's own endpoint outside the default one (else
    None). Raises NoProviderKey."""
    credentials = model_credentials(context, model_name, provider=litellm_provider_of(model_name))
    if credentials is None:
        return {}, None
    guard = credentials.workspace_id if (
        credentials.own_endpoint and credentials.workspace_id != DEFAULT_WORKSPACE_ID
        and not private_network_allowed()) else None
    return {"api_key": credentials.api_key, "api_base": credentials.base_url}, guard


@contextlib.contextmanager
def guarded(workspace_id: Optional[int]):
    """While the block runs, the outbound guard checks every httpx request, as a guarded
    model call does (outbound.GuardedCompletions); nothing for None."""
    if workspace_id is None:
        yield
        return
    _install_guard()
    token = _GUARDED.set(workspace_id)
    try:
        yield
    finally:
        _GUARDED.reset(token)


@contextlib.contextmanager
def redacting(api_key: Optional[str]):
    """An error raised in the block has the key masked (redact)."""
    try:
        yield
    except Exception as e:
        raise redact(e, api_key)


def completion(context: ModelContext, **kwargs: Any) -> Any:
    """litellm.completion(**kwargs) on the context's key (litellm_credentials)."""
    import litellm
    extra, guard = litellm_credentials(context, kwargs["model"])
    with guarded(guard), redacting(extra.get("api_key")):
        return litellm.completion(**kwargs, **extra)


def with_provider_keys(model: Any, attribute: str, context: ModelContext, model_name: str) -> Any:
    """The model, its completion client (`attribute`) taking the workspace's key on
    each call. Left as it is for a model built for no agent."""
    if not context.agent_name and context.project_id is None:
        return model
    object.__setattr__(model, attribute,
                       ProviderKeyCompletions(getattr(model, attribute), context, model_name))
    return model


# Google's own client (google.genai: ADK's native Gemini backend, File Search) goes to
# this root when the workspace's key names no endpoint. Given explicitly, so that
# GOOGLE_GEMINI_BASE_URL doesn't redirect it.
GOOGLE_API_ROOT = "https://generativelanguage.googleapis.com/"


def google_credentials(context: ModelContext, model_name: str) -> Optional[Credentials]:
    """model_credentials() for google.genai's client. Its requests go through aiohttp,
    outside the outbound guard, so a workspace's own endpoint is refused outside the
    default workspace; the gemini/ prefix reaches it through LiteLLM instead."""
    credentials = model_credentials(context, model_name)
    if (credentials is not None and credentials.own_endpoint
            and credentials.workspace_id != DEFAULT_WORKSPACE_ID and not private_network_allowed()):
        raise NoProviderKey("This workspace's google entry names a base URL, which Gemini's own "
                            "client doesn't take outside the default workspace; use a gemini/ model")
    return credentials


def google_client_kwargs(context: ModelContext, model_name: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """The arguments of a google.genai Client for this context: the workspace's key, or
    GOOGLE_API_KEY where the environment is allowed. Raises NoProviderKey, or
    RuntimeError when the environment has no key either."""
    credentials = google_credentials(context, model_name)
    if credentials is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not configured")
        return {"api_key": api_key}
    from google.genai import types
    base_url = credentials.base_url if credentials.own_endpoint else GOOGLE_API_ROOT
    return {"api_key": credentials.api_key, "vertexai": False,
            "http_options": types.HttpOptions(base_url=base_url)}


class WorkspaceGemini(Gemini):
    """ADK's native Gemini, each call made with the workspace's google key when it has
    one (google_credentials), else as Gemini is. The call goes to a Gemini built with
    that key, kept until the key changes. It is a Gemini, so ADK's built-in tools and
    context cache still see one."""

    _context: ModelContext = PrivateAttr(default_factory=ModelContext)
    _keyed: Any = PrivateAttr(default=None)

    def _with_key(self, credentials: Credentials) -> Gemini:
        key = (credentials.api_key, credentials.base_url, credentials.own_endpoint)
        if self._keyed is None or self._keyed[0] != key:
            fields = {name: getattr(self, name) for name in Gemini.model_fields
                      if name not in ("client", "client_kwargs", "base_url")}
            base_url = credentials.base_url if credentials.own_endpoint else GOOGLE_API_ROOT
            self._keyed = (key, Gemini(**fields, base_url=base_url, client_kwargs={
                "api_key": credentials.api_key, "vertexai": False}))
        return self._keyed[1]

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        credentials = await asyncio.to_thread(google_credentials, self._context, self.model)
        if credentials is None:
            responses = super().generate_content_async(llm_request, stream)
        else:
            responses = self._with_key(credentials).generate_content_async(llm_request, stream)
        with redacting(credentials.api_key if credentials else None):
            async for response in responses:
                yield response

    @contextlib.asynccontextmanager
    async def connect(self, llm_request: Any):
        """The live connection (/run_live), on the same key."""
        credentials = await asyncio.to_thread(google_credentials, self._context, self.model)
        target = super().connect(llm_request) if credentials is None else \
            self._with_key(credentials).connect(llm_request)
        async with target as live:
            yield live


def workspace_gemini(model_name: str, context: ModelContext) -> Any:
    """ADK's native Gemini for this context: a WorkspaceGemini for an agent, a plain
    Gemini for no agent (the installation's)."""
    if not context.agent_name and context.project_id is None:
        return Gemini(model=model_name)
    model = WorkspaceGemini(model=model_name)
    model._context = context
    return model
