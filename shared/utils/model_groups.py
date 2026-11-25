"""
Model groups: a name for an ordered list of models, which an administrator keeps on
Platform → Model groups (server/model_group_routes.py).

An agent names one in model_name as "group/<name>". LiteLLM has no provider called
"group", so such a name never means a model.

usable_models() says which models a call of an agent may go to, in order. It is asked on
every call (shared/utils/group_model.py on ADK, langgraph/group_model.py there), so a
changed group or key applies to running agents. save_refusal() is asked before an agent
is stored.

The rest of this module is what makes a list good for its group:

- each model is written as agents name models, and once;
- it does what the group needs (call tools, read images). What LiteLLM's price list says
  is taken; where it says nothing, the administrator's word is, kept as "confirmed";
- with Own servers only, its endpoint is the installation's own, which the provider's name
  doesn't tell (own_server_refusal).
"""

import json
import logging
import os
import re
import socket
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .models import AgentConfig, ModelGroup, Project, Workspace
from .outbound import is_internal_address
from .utils import ModelContext, _detect_provider, _is_gemini_model

logger = logging.getLogger(__name__)

PREFIX = "group/"
# Lowercase letters, digits and inner hyphens, at most 40 characters (model_groups.name)
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MAX_MODELS = 10
MODEL_NAME_MAX = 255  # agents_config.model_name

# What a group may need of its models -> LiteLLM's name for it
NEEDS = {"tools": "supports_function_calling", "images": "supports_vision"}
NEEDS_LABELS = {"tools": "call tools", "images": "read images"}

# Providers whose endpoint the installation names -> the variables that name it, and the
# address without one (create_model(), LiteLLM)
_OWN_ENDPOINTS: Dict[str, tuple] = {
    "ollama": (("OLLAMA_API_BASE",), "http://localhost:11434"),
    "ollama_chat": (("OLLAMA_API_BASE",), "http://localhost:11434"),
    "lm_studio": (("LM_STUDIO_BASE_URL",), "http://localhost:1234/v1"),
    "llamacpp": (("LLAMACPP_BASE_URL",), "http://localhost:8080/v1"),
    "llama_cpp": (("LLAMACPP_BASE_URL",), "http://localhost:8080/v1"),
    "localai": (("LOCALAI_BASE_URL",), "http://localhost:8080/v1"),
    "llamafile": (("LLAMAFILE_BASE_URL",), "http://localhost:8080/v1"),
    "hosted_vllm": (("HOSTED_VLLM_API_BASE",), None),
    "openai": (("OPENAI_BASE_URL", "OPENAI_API_BASE"), None),
}
# Called as openai/<model> on their own endpoint (create_model()), so not LiteLLM's to place
_LOCAL_SERVERS = ("lm_studio", "llamacpp", "llama_cpp", "localai", "llamafile")


class ModelGroupError(ValueError):
    """What is wrong with a group or its models, for the administrator."""


def group_of(model_name: Optional[str]) -> Optional[str]:
    """The group an agent's model_name names, or None when it names a model."""
    name = (model_name or "").strip()
    return name[len(PREFIX):] if name.startswith(PREFIX) else None


def models_of(group: ModelGroup) -> List[Dict[str, Any]]:
    """The group's models in order: [{"model", "confirmed"}]."""
    return json.loads(group.models or "[]")


def agents_of(session: Session, group: ModelGroup) -> List[AgentConfig]:
    """The agents of every workspace that name the group."""
    return (session.query(AgentConfig).filter(AgentConfig.model_name == PREFIX + group.name)
            .order_by(AgentConfig.name).all())


def workspace_names(session: Session, agents: List[AgentConfig]) -> Dict[int, str]:
    """{project id: its workspace's name} for the agents' projects."""
    projects = {a.project_id for a in agents}
    return dict(session.query(Project.id, Workspace.name)
                .join(Workspace, Workspace.id == Project.workspace_id)
                .filter(Project.id.in_(projects))) if projects else {}


def known_abilities(model_name: str) -> Dict[str, Optional[bool]]:
    """{"tools", "images"}: what LiteLLM's price list says the model does, None where it
    says nothing. Read from the list itself: get_model_info() asks an Ollama server."""
    import litellm
    if _is_gemini_model(model_name):
        keys, provider = [f"gemini/{model_name.removeprefix('models/')}"], "gemini"
    else:
        provider, _, rest = model_name.partition("/")
        keys = [model_name, rest]
    for key in keys:
        entry = litellm.model_cost.get(key)
        # "gpt-4o" under groq/ would be another provider's model
        if entry and (key == model_name or entry.get("litellm_provider") == provider):
            return {need: entry.get(field) for need, field in NEEDS.items()}
    return dict.fromkeys(NEEDS)


def _is_ollama_cloud(model_name: str) -> bool:
    tag = model_name.lower().rpartition(":")[2]
    return _detect_provider(model_name).startswith("ollama") and (tag == "cloud" or tag.endswith("-cloud"))


def own_server_refusal(model_name: str) -> Optional[str]:
    """Why the model doesn't run on the installation's own servers, or None when it does:
    its provider's endpoint is the installation's to name, and every address that endpoint
    resolves to is in the server's own network."""
    provider = "google" if _is_gemini_model(model_name) else _detect_provider(model_name)
    if provider not in _OWN_ENDPOINTS:
        return f"{model_name} is called at {provider}'s own endpoint"
    if _is_ollama_cloud(model_name):
        return f"{model_name} runs on Ollama's servers, not on this one"
    variables, default = _OWN_ENDPOINTS[provider]
    urls = [os.environ[v] for v in variables if os.getenv(v)] or ([default] if default else [])
    if not urls:
        return f"{model_name}: {' or '.join(variables)} is not set to a server of your own"
    for url in urls:
        refusal = _address_refusal(url)
        if refusal:
            return f"{model_name}: {refusal}"
    return None


def _address_refusal(url: Optional[str]) -> Optional[str]:
    """Why the URL is not in the server's own network, or None when all of its addresses are."""
    host = urlparse(url or "").hostname
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)} if host else set()
    except (socket.gaierror, UnicodeError):
        addresses = set()
    if not addresses:
        return f"{host or 'its endpoint'} does not resolve"
    if not all(is_internal_address(a) for a in addresses):
        return f"{host} is not in this server's own network"
    return None


def check_model_name(value: Any) -> str:
    """The name, as agents name models; ModelGroupError when it can't be one."""
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > MODEL_NAME_MAX or re.search(r"\s", name):
        raise ModelGroupError("Give each model a name, such as openai/gpt-4o")
    if group_of(name) is not None:
        raise ModelGroupError(f"{name} is a group; a group holds models")
    provider, _, rest = name.partition("/")
    if not _is_gemini_model(name):
        if not rest:
            raise ModelGroupError(f"{name} has no model after its provider")
        if provider in _LOCAL_SERVERS:
            return name
    elif rest:
        return name  # models/<a Gemini model>
    import litellm
    try:
        placed = litellm.get_llm_provider(name)[1]
    except Exception:
        if not rest:
            return name  # an unknown bare name is called as a Gemini model
        raise ModelGroupError(f"LiteLLM has no provider called {provider}")
    if not rest and placed not in ("gemini", "vertex_ai"):
        raise ModelGroupError(f"A name without a provider is called as a Gemini model: write {placed}/{name}")
    return name


def check_models(value: Any, needs: List[str], own_servers_only: bool) -> List[Dict[str, Any]]:
    """The list as stored, when every model in it is good for the group."""
    if not isinstance(value, list) or any(not isinstance(m, dict) for m in value):
        raise ModelGroupError("models must be a list of {model, confirmed}")
    if len(value) > MAX_MODELS:
        raise ModelGroupError(f"A group has at most {MAX_MODELS} models")
    from .langgraph.model_factory import litellm_model_name
    models: List[Dict[str, Any]] = []
    for item in value:
        name = check_model_name(item.get("model"))
        # A run tells its models apart by the name LiteLLM is called with
        if any(litellm_model_name(m["model"]) == litellm_model_name(name) for m in models):
            raise ModelGroupError(f"{name} is in the list twice")
        said = item.get("confirmed") or []
        if not isinstance(said, list) or any(s not in NEEDS for s in said):
            raise ModelGroupError(f"confirmed must be a list of {', '.join(NEEDS)}")
        known = known_abilities(name)
        for need in needs:
            if known[need] is False:
                raise ModelGroupError(f"{name} does not {NEEDS_LABELS[need]}, and this group needs it to")
            if known[need] is None and need not in said:
                raise ModelGroupError(f"LiteLLM doesn't say that {name} can {NEEDS_LABELS[need]}: "
                                      "confirm it if you know that it can")
        if own_servers_only:
            refusal = own_server_refusal(name)
            if refusal:
                raise ModelGroupError(refusal)
        models.append({"model": name, "confirmed": [n for n in NEEDS if n in said and known[n] is None]})
    return models


def _environment_has_key(model_name: str) -> bool:
    """Whether the installation's environment has what the model's provider asks for. A
    local server asks for nothing, and what LiteLLM doesn't know is tried."""
    if _is_gemini_model(model_name):
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
                    or os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"))
    provider = _detect_provider(model_name)
    if provider in _OWN_ENDPOINTS and provider != "openai":
        return True
    import litellm
    try:
        return not litellm.validate_environment(model=model_name).get("missing_keys")
    except Exception:
        return True


def usable_models(name: str, context: ModelContext) -> List[str]:
    """The group's models that a call in this context may go to, in order: those its
    workspace has a key for, its own or the installation's where it may use that. With
    Own servers only, the endpoint is checked again as the call would be sent.

    ModelGroupError when there is no such group or it has no models, NoProviderKey when
    the workspace can call none of them."""
    from .database_client import get_database_client
    from .provider_keys import NoProviderKey, model_credentials
    db = get_database_client()
    session = db.get_session() if db else None
    if session is None:
        raise ModelGroupError(f"No database to look up the model group {PREFIX}{name}")
    try:
        group = session.query(ModelGroup).filter(ModelGroup.name == name).first()
        if group is None:
            raise ModelGroupError(f"There is no model group {PREFIX}{name}")
        own_servers_only = bool(group.own_servers_only)
        models = [m["model"] for m in models_of(group)]
    finally:
        session.close()
    if not models:
        raise ModelGroupError(f"The model group {PREFIX}{name} has no models")
    usable = list(_usable(name, models, own_servers_only, lambda provider: model_credentials(context, None, provider)))
    if not usable:
        raise NoProviderKey(_none_usable(name, own_servers_only))
    return usable


def _none_usable(name: str, own_servers_only: bool) -> str:
    if own_servers_only:
        return f"No model of {PREFIX}{name} runs on its own servers for this workspace"
    return f"This workspace has no key for any model of {PREFIX}{name}"


def _usable(name: str, models: List[str], own_servers_only: bool,
            credentials_for: Callable[[str], Any]) -> Iterator[str]:
    """Those of the models that a call may go to. credentials_for(provider) is asked once
    for each provider: Credentials, None for the environment, or NoProviderKey raised."""
    from .provider_keys import Credentials, NoProviderKey, provider_of
    by_provider: Dict[str, Any] = {}
    for model in models:
        provider = provider_of(model)
        if provider not in by_provider:
            try:
                by_provider[provider] = credentials_for(provider)
            except NoProviderKey as e:
                by_provider[provider] = e
        credentials = by_provider[provider]
        if isinstance(credentials, NoProviderKey):
            continue
        keyed = isinstance(credentials, Credentials)
        if own_servers_only:
            if not keyed:
                refusal = own_server_refusal(model)
            elif _is_ollama_cloud(model):
                refusal = f"{model} runs on Ollama's servers"
            else:
                refusal = _address_refusal(credentials.base_url)
            if refusal:
                logger.warning("Model group %s%s: %s left out (%s)", PREFIX, name, model, refusal)
                continue
        if not keyed and not _environment_has_key(model):
            continue
        yield model


def groups_for(session: Session, workspace_id: int) -> List[Dict[str, Any]]:
    """The groups as an agent of the workspace may choose them: the model that would
    answer now, or the reason why none would. A group without models can't be stored
    (save_refusal); one the workspace has no key for can."""
    from .provider_keys import workspace_credentials
    found = []
    for group in session.query(ModelGroup).order_by(ModelGroup.id):
        models = [m["model"] for m in models_of(group)]
        own = bool(group.own_servers_only)
        answers = next(_usable(group.name, models, own,
                               lambda provider: workspace_credentials(session, workspace_id, provider)), None)
        reason = None
        if answers is None:
            reason = _none_usable(group.name, own) if models else f"The model group {PREFIX}{group.name} has no models"
        found.append({"name": group.name, "model_name": PREFIX + group.name, "label": group.label,
                      "description": group.description or "", "own_servers_only": own,
                      "has_models": bool(models), "answers": answers, "reason": reason})
    return found


def answering_model(model_name: Optional[str], context: ModelContext) -> str:
    """The model a call would go to now: the first usable one of a group, "" when there is
    none, and a model as it is. For what depends on the model before it is called."""
    group = group_of(model_name)
    if group is None:
        return model_name or ""
    try:
        return usable_models(group, context)[0]
    except Exception as e:
        logger.info("No model answers for %s: %s", model_name, e)
        return ""


def save_refusal(session: Session, config: Dict[str, Any], stored: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Why an agent's config can't be stored for the group it names, or None. A field the
    config leaves out is the stored one's."""
    def value(field: str) -> str:
        found = config[field] if field in config else (stored or {}).get(field)
        return found.strip() if isinstance(found, str) else ""

    if group_of(value("fallback_model")) is not None:
        return "A fallback model is a model; a group brings its own fallbacks"
    name = group_of(value("model_name"))
    if name is None:
        return None
    if value("model_base_url") or value("model_api_key"):
        return "A model group takes no Model Base URL or Model API Key; those belong to one model"
    group = session.query(ModelGroup).filter(ModelGroup.name == name).first()
    if group is None:
        return f"There is no model group {PREFIX}{name}; choose another group or a model"
    if not models_of(group):
        return f"The model group {PREFIX}{name} has no models; choose another group or a model"
    return None


def leaves_own_servers(parent_model: Optional[str], model_name: str) -> Optional[str]:
    """Why an agent whose group has Own servers only can't have a sub-agent on this model,
    or None when it can."""
    parent, group = group_of(parent_model), group_of(model_name)
    if parent is None or group == parent:
        return None
    from .database_client import get_database_client
    db = get_database_client()
    session = db.get_session() if db else None
    if session is None:
        return f"No database to look up the model group {PREFIX}{parent}"
    try:
        own = {g.name for g in session.query(ModelGroup).filter(ModelGroup.own_servers_only.is_(True))}
    finally:
        session.close()
    if parent in own and group not in own:
        return f"An agent of {PREFIX}{parent} stays on its own servers, and {model_name} is not in such a group"
    return None
