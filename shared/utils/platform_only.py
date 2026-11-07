"""
Agent features that run code with the server's rights, or read its environment, stay
the platform's (documents/design/workspaces.md, "What stays platform-only"):

- the code executor tool (tool_config "code_executor"), which is not a sandbox;
- an MCP server started as a local command (stdio: an entry with no url);
- ${VAR} in model_api_key, model_base_url or mcp_servers_config, which reads the
  server's environment: a base URL of one's own with the key ${MATE_SECRET_KEY} would
  send that secret to it;
- a hardcoded agent: the hardcoded flag, or a name that an agents/<name>/ folder
  already has, whose agent.py the agent server loads;
- a tool that acts with the installation's own credentials, shared by every
  workspace: Google Drive, CV Tools (which read Drive) and Google Calendar (its
  service account) and Supabase Storage (one key, one bucket).

Two checks:
- On save, features_added() lists what a config gives an agent that it didn't have,
  or changes: a new command for an existing stdio server runs new code, and a new
  base URL beside a ${VAR} key sends the variable there, as it would a stored key the
  config keeps without naming it. The dashboard routes refuse
  it to a caller who is no platform administrator, so a workspace admin keeps what a
  platform administrator set up as it is, and can't add more.
- At run time, may_run_code() refuses the code executor, stdio MCP servers and the
  server-credential tools to an agent outside the default workspace, until phase 3
  lets a platform administrator enable them per workspace.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .utils import ENV_PLACEHOLDER

logger = logging.getLogger(__name__)

CODE_EXECUTOR = "the code executor"
STDIO_MCP = "an MCP server started as a command"
ENV_REFERENCE = "a ${VAR} reference"
HARDCODED = "a hardcoded agent"
SERVER_CREDENTIALS = "a tool that uses the server's own credentials"
# tool_config keys of the tools that act with the installation's credentials
SERVER_CREDENTIAL_TOOLS = ("google_drive", "cv_tools", "google_calendar", "supabase_storage")
KEPT_KEY = "a new base URL for its stored API key (enter the key again)"


def _parsed(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value) if value.strip() else None
        except ValueError:
            return None
    return value


def _as_run(servers: Any) -> Any:
    """mcp_servers_config as the runtimes read it before resolving ${VAR}: they decode
    an entry's headers, args and env once more when it is a string (_parse_json_field
    in tools/mcp_tools.py, _parse_json_maybe in langgraph/mcp_client.py), so a
    placeholder spelled with JSON escapes there is only a placeholder after that."""
    entries = servers.get("mcpServers") if isinstance(servers, dict) else None
    if not isinstance(entries, dict):
        return servers
    decoded = {}
    for name, entry in entries.items():
        if isinstance(entry, dict):
            # Text that isn't JSON stays as it is, so a literal ${VAR} in it is seen
            entry = {key: (_parsed(value) or value) if key in ("headers", "args", "env") else value
                     for key, value in entry.items()}
        decoded[name] = entry
    return {**servers, "mcpServers": decoded}


def _has_placeholder(value: Any) -> bool:
    return bool(ENV_PLACEHOLDER.search(json.dumps(value)))


def _env_references(config: Dict[str, Any], servers: Any) -> Dict[str, Any]:
    """Each place that holds a ${VAR}, with where its value is sent: the model's key and
    base URL together, and each MCP server entry whole. Editing a server or a field
    without a placeholder changes none of them."""
    references: Dict[str, Any] = {}
    model = (config.get("model_api_key"), config.get("model_base_url"))
    if _has_placeholder(model):
        references["model"] = model
    run = _as_run(servers)
    if isinstance(run, dict) and isinstance(run.get("mcpServers"), dict):
        for name, entry in run["mcpServers"].items():
            if _has_placeholder(entry):
                references[f"mcp:{name}"] = entry
        run = {key: value for key, value in run.items() if key != "mcpServers"}
    if run and _has_placeholder(run):
        references["mcp"] = run
    return references


def _defining(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{feature: the part of the config that defines it} for each platform-only feature
    an agent config (a dict of its columns) has."""
    if not config:
        return {}
    found: Dict[str, Any] = {}
    tools = _parsed(config.get("tool_config"))
    # The tool factory builds a tool for any value but None
    if isinstance(tools, dict) and tools.get("code_executor") is not None:
        found[CODE_EXECUTOR] = tools["code_executor"]
    if isinstance(tools, dict):
        # Its value too: a calendar_id names which of the account's calendars
        credential_tools = {key: tools[key] for key in SERVER_CREDENTIAL_TOOLS
                            if tools.get(key) is not None}
        if credential_tools:
            found[SERVER_CREDENTIALS] = credential_tools
    servers = _parsed(config.get("mcp_servers_config"))
    entries = servers.get("mcpServers") if isinstance(servers, dict) else None
    if isinstance(entries, dict):
        stdio = {name: entry for name, entry in entries.items()
                 if not isinstance(entry, dict) or not entry.get("url")}
        if stdio:
            found[STDIO_MCP] = stdio
    references = _env_references(config, servers)
    if references:
        found[ENV_REFERENCE] = references
    if config.get("hardcoded") is True or config.get("hardcoded") == 1:
        found[HARDCODED] = True
    return found


def features_of(config: Optional[Dict[str, Any]]) -> Set[str]:
    """The platform-only features an agent config has."""
    return set(_defining(config))


def features_added(config: Dict[str, Any], before: Optional[Dict[str, Any]] = None) -> Set[str]:
    """The features that saving `config` over `before` (None for a new agent) adds or
    changes. A field the config leaves out keeps its value from before."""
    merged = {**(before or {}), **config}
    after, earlier = _defining(merged), _defining(before)
    added = {feature for feature, value in after.items()
             if feature not in earlier or earlier[feature] != value}
    # A key the config keeps without naming it, sent to a base URL of the caller's
    # choosing, would reach that host: changing the URL takes the key again
    if (before and before.get("model_api_key") and "model_api_key" not in config
            and merged.get("model_base_url")
            and merged["model_base_url"] != before.get("model_base_url")):
        added.add(KEPT_KEY)
    return added


def is_agent_name(name: Optional[str]) -> bool:
    """True for a name ADK accepts: an identifier, at most 255 characters. Such a name
    is one folder under agents/, never '..' or a path, and holds no LIKE wildcard but _."""
    return bool(name) and name.isidentifier() and len(name) <= 255


def has_code_folder(agents_dir: Path, name: Optional[str]) -> bool:
    """True if agents/<name>/ exists: a hardcoded agent's code, or a folder a deleted
    agent left behind. An agent of that name would run what is in it."""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return False
    return (agents_dir / name).is_dir()


def may_run_code(agent_name: Optional[str], project_id: Optional[int] = None) -> bool:
    """True if the agent may have the code executor, stdio MCP servers and the
    server-credential tools: an agent of the default workspace, or with no config (a
    hardcoded agent). False when the check
    can't run, as the tools are refused rather than handed out by accident.

    A config that names its project_id is judged by that project's workspace, not by
    its name: a subagent made by delegation has its parent's project and a name with
    no row (tools/subagent_delegation_tool.py). A project that doesn't exist refuses."""
    from .database_client import get_database_client
    from .models import DEFAULT_WORKSPACE_ID
    from .workspaces import agent_workspace, workspace_of_project

    if not agent_name and project_id is None:
        return True
    try:
        session = get_database_client().get_session()
        if not session:
            logger.error("Platform-only check for %s: no database session; refusing", agent_name)
            return False
        try:
            if project_id is not None:
                return workspace_of_project(session, project_id) == DEFAULT_WORKSPACE_ID
            workspace_id = agent_workspace(session, agent_name)
        finally:
            session.close()
    except Exception as e:
        logger.error("Platform-only check for %s failed (%s); refusing", agent_name, e)
        return False
    return workspace_id in (None, DEFAULT_WORKSPACE_ID)
