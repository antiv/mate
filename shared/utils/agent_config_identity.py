"""
Which configuration an agent was built from, for traces.

When an agent is built, record_loaded() remembers its config hash and, when the
config is one a version in agent_config_versions holds, that version's id and
number. The gen_ai.inference span of each model call carries them, so Trace
Replay can say which version answered, and that two replays of one agent ran on
different configs.

What is recorded is what was loaded, not what the row says now: the running
agent tree is older than the database until it is reloaded. Versions are only
taken by dashboard saves; a config written another way (the create_agent tool,
template import, SQL) matches no version and is recorded by its hash alone.
"""

import hashlib
import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Never hashed: a hash of a short key could be brute-forced from the span
_UNHASHED = ("model_api_key",)


def config_snapshot(config: Any) -> Dict[str, Any]:
    """A plain dict snapshot of an AgentConfig row (no relationships)."""
    return {
        'name': config.name,
        'type': config.type,
        'model_name': config.model_name,
        'ai_disclosure': config.ai_disclosure,
        'ai_disclosure_waiver': config.ai_disclosure_waiver,
        'model_base_url': config.model_base_url,
        'model_api_key': config.model_api_key,
        'fallback_model': config.fallback_model,
        'description': config.description,
        'instruction': config.instruction,
        'mcp_servers_config': config.mcp_servers_config,
        'parent_agents': config.parent_agents,
        'allowed_for_roles': config.allowed_for_roles,
        'tool_config': config.tool_config,
        'planner_config': config.planner_config,
        'max_iterations': config.max_iterations,
        'generate_content_config': config.generate_content_config,
        'input_schema': config.input_schema,
        'output_schema': config.output_schema,
        'include_contents': config.include_contents,
        'guardrail_config': config.guardrail_config,
        'disabled': config.disabled,
        'hardcoded': config.hardcoded,
        'project_id': config.project_id,
    }


def config_hash(snapshot: Dict[str, Any], keys: Optional[set] = None) -> str:
    """A short, stable hash of a snapshot, over `keys` when given (else all of them)."""
    fields = {k: v for k, v in snapshot.items()
              if k not in _UNHASHED and (keys is None or k in keys)}
    canonical = json.dumps(fields, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def identify(session: Any, config: Any) -> Dict[str, Any]:
    """{hash, version_id, version} for an AgentConfig row; the version ones None when
    no stored version holds this config. Versions are compared newest first, each
    over its own keys, since an older snapshot lacks fields added since."""
    from .models import AgentConfigVersion

    snapshot = config_snapshot(config)
    identity = {"hash": config_hash(snapshot), "version_id": None, "version": None}
    versions = (session.query(AgentConfigVersion)
                .filter(AgentConfigVersion.agent_config_id == config.id)
                .order_by(AgentConfigVersion.version_number.desc()).all())
    for version in versions:
        stored = version.get_snapshot()
        if stored and config_hash(stored) == config_hash(snapshot, set(stored)):
            identity.update(version_id=version.id, version=version.version_number)
            break
    return identity


_loaded: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def record_loaded(agent_name: str, identity: Dict[str, Any]) -> None:
    with _lock:
        _loaded[agent_name] = dict(identity)


def loaded(agent_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """The identity of the config the running agent was built from, if recorded."""
    if not agent_name:
        return None
    with _lock:
        identity = _loaded.get(agent_name)
    return dict(identity) if identity else None


def record_for_row(db_client: Any, config: Any, *names: str) -> None:
    """Identify `config` and record it under each name the built agent runs as. Never raises."""
    session = db_client.get_session() if db_client else None
    if not session:
        return
    try:
        identity = identify(session, config)
        for name in names:
            record_loaded(name, identity)
    except Exception as e:
        logger.warning(f"Could not identify the config of agent {config.name}: {e}")
    finally:
        session.close()
