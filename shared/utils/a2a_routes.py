"""
A2A (agent-to-agent) routes for every agent on the ADK runtime.

Each agent gets a JSON-RPC endpoint at /a2a/{agent} and its card at
/a2a/{agent}/.well-known/agent-card.json. The card and the routes are built
through google.adk.a2a._compat, which supports both a2a-sdk 0.3.x and 1.x: on
1.x the routes also accept the 0.3 method names (message/send, tasks/get, ...),
which the MCP bridge and existing callers send.
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# The address the default card names. Callers reach the agent through the auth
# server, which forwards /a2a/* to the agent server.
DEFAULT_BASE_URL = "http://localhost:8000"


def default_card_data(app_name: str, base_url: str = DEFAULT_BASE_URL) -> Dict[str, Any]:
    """The card of an agent without an agents/<name>/agent.json, in the 0.3 JSON form.

    parse_agent_card turns the 0.3 form into a 1.x card (url → supportedInterfaces)
    on 1.x, so the same dict, and the agent.json files written for 0.3, serve both.
    """
    return {
        "name": app_name,
        "description": f"A2A-enabled {app_name} agent",
        "version": "1.0.0",
        "url": f"{base_url.rstrip('/')}/a2a/{app_name}",
        "protocolVersion": "0.3.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "general",
                "name": "general",
                "description": f"General capabilities of {app_name}",
                "tags": ["general", "a2a"],
            }
        ],
    }


def card_data_for(app_name: str, agents_dir: Optional[Path]) -> Dict[str, Any]:
    """The default card, with agents/<name>/agent.json merged over it when there is one."""
    data = default_card_data(app_name)
    if agents_dir is not None:
        path = Path(agents_dir) / app_name / "agent.json"
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data.update(json.load(f))
            except Exception as e:
                logger.warning("A2A: could not read %s: %s", path, e)
    return data


def register_a2a_routes(app: Any, app_names: Iterable[str],
                        executor_factory: Callable[[str], Any],
                        agents_dir: Optional[Path] = None) -> int:
    """Mount /a2a/{name} and its card for each agent. Returns how many were mounted.

    ``executor_factory(name)`` returns the agent's A2A executor. An agent whose
    card or routes cannot be built is skipped and logged; the others still mount.
    Raises ImportError when the A2A dependencies are missing.
    """
    from a2a.server.tasks import InMemoryTaskStore
    from google.adk.a2a import _compat

    task_store = InMemoryTaskStore()
    mounted = 0
    for app_name in app_names:
        try:
            card = _compat.parse_agent_card(card_data_for(app_name, agents_dir))
            _compat.attach_a2a_routes_to_app(
                app,
                agent_card=card,
                agent_executor=executor_factory(app_name),
                task_store=task_store,
                prefix=f"/a2a/{app_name}",
            )
            mounted += 1
        except Exception as e:
            logger.error("A2A: could not set up agent %s: %s", app_name, e)
    return mounted
