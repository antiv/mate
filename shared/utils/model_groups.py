"""
Model groups: a name for an ordered list of models, which an administrator keeps on
Platform → Model groups (server/model_group_routes.py).

An agent is to name one in model_name as "group/<name>". LiteLLM has no provider called
"group", so such a name never means a model. Nothing resolves it yet.

This module holds what makes a list good for its group:

- each model is written as agents name models, and once;
- it does what the group needs (call tools, read images). What LiteLLM's price list says
  is taken; where it says nothing, the administrator's word is, kept as "confirmed";
- with Own servers only, its endpoint is the installation's own, which the provider's name
  doesn't tell (own_server_refusal).
"""

import json
import os
import re
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .models import AgentConfig, ModelGroup, Project, Workspace
from .outbound import is_internal_address
from .utils import _detect_provider, _is_gemini_model

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


def own_server_refusal(model_name: str) -> Optional[str]:
    """Why the model doesn't run on the installation's own servers, or None when it does:
    its provider's endpoint is the installation's to name, and every address that endpoint
    resolves to is in the server's own network."""
    provider = "google" if _is_gemini_model(model_name) else _detect_provider(model_name)
    if provider not in _OWN_ENDPOINTS:
        return f"{model_name} is called at {provider}'s own endpoint"
    if provider.startswith("ollama"):
        tag = model_name.lower().rpartition(":")[2]
        if tag == "cloud" or tag.endswith("-cloud"):
            return f"{model_name} runs on Ollama's servers, not on this one"
    variables, default = _OWN_ENDPOINTS[provider]
    urls = [os.environ[v] for v in variables if os.getenv(v)] or ([default] if default else [])
    if not urls:
        return f"{model_name}: {' or '.join(variables)} is not set to a server of your own"
    for url in urls:
        host = urlparse(url).hostname
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(host, None)} if host else set()
        except (socket.gaierror, UnicodeError):
            addresses = set()
        if not addresses:
            return f"{model_name}: {host or 'its endpoint'} does not resolve"
        if not all(is_internal_address(a) for a in addresses):
            return f"{model_name}: {host} is not in this server's own network"
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
    models: List[Dict[str, Any]] = []
    for item in value:
        name = check_model_name(item.get("model"))
        if any(m["model"] == name for m in models):
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
