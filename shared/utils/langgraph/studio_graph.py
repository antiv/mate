"""
Graph factory for LangGraph Studio (dev-only bridge).

`langgraph dev --allow-blocking` loads this factory (wired in langgraph.json)
and serves the graph over the LangGraph Server protocol on port 2024, which
Studio connects to for visualization, step inspection and time travel.
--allow-blocking is required because MATE's DB layer is synchronous, which the
dev server's blocking-call detector would otherwise reject.

Pick the agent with the STUDIO_AGENT env var (defaults to the first entry of
AGENTS_LIST, then to the first enabled top-level agent in the database).

Notes:
- The graph is built WITHOUT the MATE checkpointer — Studio's dev server
  provides its own persistence.
- Runs started from Studio have no MATE run context, so tools receive
  tool_context=None: chat/LLM/transfer flows work, but tools that need the
  MATE session (artifacts, session state, user profile) degrade gracefully.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _pick_agent_name() -> str:
    name = os.getenv("STUDIO_AGENT")
    if name:
        return name.strip()
    agents_list = os.getenv("AGENTS_LIST", "")
    if agents_list.strip():
        return agents_list.split(",")[0].strip()
    from shared.utils.langgraph.api import list_apps
    apps = list_apps()
    if not apps:
        raise RuntimeError("No agents found — set STUDIO_AGENT or AGENTS_LIST")
    return apps[0]


async def make_graph():
    from shared.utils.langgraph.agent_builder import AgentBuilder

    agent_name = _pick_agent_name()
    logger.info(f"[Studio] Building graph for agent '{agent_name}' (override with STUDIO_AGENT)")
    built = await AgentBuilder()._build(agent_name, use_checkpointer=False)
    return built.graph
