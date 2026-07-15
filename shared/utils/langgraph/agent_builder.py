"""
Builds compiled LangGraph graphs from agents_config DB rows.

v1 supports `llm` type agents (create_react_agent). Sub-agent delegation uses a
transfer_to_agent handoff tool (same name/semantics as ADK): each agent in the
tree becomes a node in a parent StateGraph, and a transfer persists across
turns via the checkpointed `current_agent` key. `graph`/`loop` workflow types
are reported as unsupported and produce a friendly error event instead of
failing.
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CHECKPOINT_DB_PATH = PROJECT_ROOT / "lg_checkpoints.db"

TRANSFER_TOOL_NAME = "transfer_to_agent"


class AgentNotFoundError(Exception):
    pass


class UnsupportedAgentTypeError(Exception):
    def __init__(self, agent_name: str, agent_type: str):
        self.agent_name = agent_name
        self.agent_type = agent_type
        super().__init__(f"Agent '{agent_name}' has type '{agent_type}' which is not supported by the langgraph runtime")


class BuiltAgent:
    """A compiled graph plus per-agent metadata for the run loop."""

    def __init__(self, name: str, graph: Any, model_name: str, config: Dict[str, Any],
                 guardrail_engines: Optional[Dict[str, Any]] = None,
                 model_names: Optional[Dict[str, str]] = None):
        self.name = name
        self.graph = graph
        self.model_name = model_name
        self.config = config
        self.guardrail_engines = guardrail_engines or {}
        # author (agent name) → model name, for token logging in multi-agent trees
        self.model_names = model_names or {name: model_name}


_checkpointer = None


async def get_checkpointer():
    """Shared checkpointer holding graph state (messages, interrupts) per thread/session."""
    global _checkpointer
    if _checkpointer is None:
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            conn = await aiosqlite.connect(str(CHECKPOINT_DB_PATH))
            _checkpointer = AsyncSqliteSaver(conn)
            await _checkpointer.setup()
            logger.info(f"LangGraph checkpointer: sqlite at {CHECKPOINT_DB_PATH}")
        except Exception as e:
            from langgraph.checkpoint.memory import InMemorySaver
            logger.warning(f"Falling back to in-memory checkpointer (no session persistence): {e}")
            _checkpointer = InMemorySaver()
    return _checkpointer


def _load_agent_config(agent_name: str) -> Optional[Dict[str, Any]]:
    from shared.utils.database_client import get_database_client
    from shared.utils.models import AgentConfig

    db_client = get_database_client()
    session = db_client.get_session() if db_client else None
    if not session:
        raise RuntimeError("Database not available")
    try:
        row = session.query(AgentConfig).filter(
            AgentConfig.name == agent_name,
            AgentConfig.disabled == False
        ).first()
        return row.to_dict() if row else None
    finally:
        session.close()


def _load_child_configs(parent_name: str) -> List[Dict[str, Any]]:
    """All enabled agents that list parent_name among their parent_agents."""
    from shared.utils.database_client import get_database_client
    from shared.utils.models import AgentConfig

    db_client = get_database_client()
    session = db_client.get_session() if db_client else None
    if not session:
        return []
    try:
        rows = session.query(AgentConfig).filter(AgentConfig.disabled == False).all()
        return [row.to_dict() for row in rows if parent_name in row.get_parent_agents()]
    finally:
        session.close()


def _json_field(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    raw = config.get(key)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _make_prompt(instruction: Optional[str], transfer_note: Optional[str] = None):
    """Prompt runnable: instruction + optional transfer note + per-user profile block."""
    from langchain_core.messages import SystemMessage
    from langchain_core.runnables import RunnableLambda
    from shared.utils.langgraph.hooks import get_user_profile_block

    def build_messages(state, config=None):
        sections = []
        if instruction:
            sections.append(instruction)
        if transfer_note:
            sections.append(transfer_note)
        user_id = ((config or {}).get("configurable") or {}).get("user_id")
        profile_block = get_user_profile_block(user_id)
        if profile_block:
            sections.append(profile_block)
        messages = list(state["messages"])
        if sections:
            return [SystemMessage(content="\n\n".join(sections))] + messages
        return messages

    return RunnableLambda(build_messages)


def _build_transfer_note(agent_name: str, tree: Dict[str, Dict[str, Any]],
                         children_of: Dict[str, List[str]]) -> Optional[str]:
    """System-prompt transfer instructions, mirroring ADK's wording
    (google/adk/flows/llm_flows/agent_transfer.py) so routing behaves the same:
    the sub-agent list with descriptions, an explicit rule to call the tool
    instead of answering, and a separate back-transfer line for parents."""
    children = children_of.get(agent_name, [])
    parents = [parent for parent, kids in children_of.items() if agent_name in kids]
    if not children and not parents:
        return None

    allowed = sorted(children + parents)
    formatted_names = ", ".join(f"`{name}`" for name in allowed)

    sections = []
    if children:
        agents_info = "\n".join(
            f"\nAgent name: {child}\nAgent description: {tree[child].get('description') or ''}"
            for child in children
        )
        sections.append(f"You have a list of other agents to transfer to:\n{agents_info}")
    sections.append(
        "If you are the best to answer the question according to your description, "
        "you can answer it.\n\n"
        "If another agent is better for answering the question according to its "
        f"description, call `{TRANSFER_TOOL_NAME}` function to transfer the question "
        "to that agent. When transferring, do not generate any text other than the "
        "function call."
    )
    sections.append(f"**NOTE**: the only available agents for `{TRANSFER_TOOL_NAME}` "
                    f"function are {formatted_names}.")
    for parent in parents:
        sections.append(
            "If neither you nor the other agents are best for the question, "
            f"transfer to your parent agent {parent}."
        )
    return "\n\n".join(sections)


def _make_handoff_tool(source_name: str, targets: Dict[str, str]):
    """The transfer_to_agent tool (ADK-compatible name/args) for one agent node."""
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import InjectedToolCallId, StructuredTool
    from langgraph.types import Command

    def transfer_to_agent(agent_name: str,
                          tool_call_id: Annotated[str, InjectedToolCallId]) -> Any:
        if agent_name not in targets:
            return f"Unknown agent '{agent_name}'. Available agents: {sorted(targets)}"
        from shared.utils.langgraph.hooks import check_rbac_message
        from shared.utils.langgraph.tool_adapter import get_run_context
        run_context = get_run_context()
        if run_context is not None:
            denied = check_rbac_message(run_context.user_id, agent_name)
            if denied:
                return denied
        return Command(
            goto=agent_name,
            graph=Command.PARENT,
            update={
                "current_agent": agent_name,
                "messages": [ToolMessage(content=f"Transferred to agent '{agent_name}'.",
                                         name=TRANSFER_TOOL_NAME, tool_call_id=tool_call_id)],
            },
        )

    target_lines = "; ".join(f"{name}: {description or 'no description'}"
                             for name, description in sorted(targets.items()))
    return StructuredTool.from_function(
        func=transfer_to_agent,
        name=TRANSFER_TOOL_NAME,
        description=f"Transfer the conversation to another agent. Available agents — {target_lines}",
    )


class AgentBuilder:
    """Caches compiled graphs per app name; invalidated by the reload endpoints."""

    def __init__(self):
        self._cache: Dict[str, BuiltAgent] = {}

    def invalidate(self, agent_name: str) -> None:
        self._cache.pop(agent_name, None)

    def invalidate_all(self) -> None:
        self._cache.clear()

    async def get(self, app_name: str) -> BuiltAgent:
        if app_name in self._cache:
            return self._cache[app_name]
        built = await self._build(app_name)
        self._cache[app_name] = built
        return built

    async def _build(self, app_name: str, use_checkpointer: bool = True) -> BuiltAgent:
        """use_checkpointer=False builds a graph without persistence — required when
        the graph runs under a host that provides its own (e.g. LangGraph Studio)."""
        root_config = _load_agent_config(app_name)
        if root_config is None:
            raise AgentNotFoundError(f"No enabled agent config found for '{app_name}'")

        agent_type = (root_config.get("type") or "llm").lower()
        if agent_type != "llm":
            raise UnsupportedAgentTypeError(app_name, agent_type)

        # Collect the whole tree (children may themselves have children)
        tree: Dict[str, Dict[str, Any]] = {}
        children_of: Dict[str, List[str]] = {}
        self._collect_tree(root_config, tree, children_of)

        checkpointer = await get_checkpointer() if use_checkpointer else None
        guardrail_engines = self._build_guardrail_engines(tree)
        model_names = {name: config.get("model_name") for name, config in tree.items()}

        if len(tree) == 1:
            graph = await self._build_react_agent(root_config, children_of, checkpointer=checkpointer)
        else:
            graph = await self._build_multi_agent_graph(app_name, tree, children_of, checkpointer)

        logger.info(f"Built LangGraph agent '{app_name}' ({len(tree)} agent(s) in tree)")
        return BuiltAgent(name=app_name, graph=graph, model_name=root_config.get("model_name"),
                          config=root_config, guardrail_engines=guardrail_engines,
                          model_names=model_names)

    def _collect_tree(self, config: Dict[str, Any], tree: Dict[str, Dict[str, Any]],
                      children_of: Dict[str, List[str]]) -> None:
        name = config["name"]
        if name in tree:
            return
        tree[name] = config
        children = []
        for child_config in _load_child_configs(name):
            child_type = (child_config.get("type") or "llm").lower()
            if child_type != "llm":
                logger.warning(f"Skipping sub-agent '{child_config['name']}' of '{name}': "
                               f"type '{child_type}' is not supported by the langgraph runtime")
                continue
            children.append(child_config["name"])
            self._collect_tree(child_config, tree, children_of)
        children_of[name] = children

    def _build_guardrail_engines(self, tree: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        from shared.utils.langgraph.hooks import build_guardrail_engines
        return build_guardrail_engines(tree)

    def _transfer_targets(self, agent_name: str, tree: Dict[str, Dict[str, Any]],
                          children_of: Dict[str, List[str]]) -> Dict[str, str]:
        """Allowed transfer targets: own sub-agents plus parents within the tree (back-transfer)."""
        targets = {}
        for child in children_of.get(agent_name, []):
            targets[child] = tree[child].get("description") or ""
        for parent, children in children_of.items():
            if agent_name in children:
                targets[parent] = tree[parent].get("description") or ""
        return targets

    async def _build_react_agent(self, config: Dict[str, Any],
                                 children_of: Dict[str, List[str]],
                                 checkpointer: Any = None,
                                 extra_tools: Optional[List[Any]] = None,
                                 transfer_note: Optional[str] = None) -> Any:
        from langgraph.prebuilt import create_react_agent
        from shared.utils.langgraph.model_factory import create_chat_model

        app_name = config["name"]
        planner_config = _json_field(config, "planner_config")
        if planner_config:
            logger.debug(f"Agent '{app_name}': planner_config is not supported by the langgraph runtime; ignoring")

        model = create_chat_model(
            config.get("model_name"),
            generate_content_config=_json_field(config, "generate_content_config"),
        )
        tools = await self._build_tools(config)
        if extra_tools:
            tools.extend(extra_tools)

        return create_react_agent(
            model,
            tools=tools,
            prompt=_make_prompt(config.get("instruction"), transfer_note),
            name=app_name,
            checkpointer=checkpointer,
        )

    async def _build_multi_agent_graph(self, root_name: str, tree: Dict[str, Dict[str, Any]],
                                       children_of: Dict[str, List[str]], checkpointer: Any) -> Any:
        from langgraph.graph import END, START, MessagesState, StateGraph

        # total=False: current_agent is routing state set by transfer_to_agent —
        # it must not be a required input field (e.g. in LangGraph Studio's form)
        class MultiAgentState(MessagesState, total=False):
            current_agent: str

        builder = StateGraph(MultiAgentState)

        for agent_name, config in tree.items():
            targets = self._transfer_targets(agent_name, tree, children_of)
            extra_tools = []
            transfer_note = None
            if targets:
                extra_tools.append(_make_handoff_tool(agent_name, targets))
                transfer_note = _build_transfer_note(agent_name, tree, children_of)
            node_graph = await self._build_react_agent(
                config, children_of, checkpointer=None,
                extra_tools=extra_tools, transfer_note=transfer_note)
            builder.add_node(agent_name, node_graph)
            builder.add_edge(agent_name, END)

        def route_entry(state) -> str:
            current = state.get("current_agent")
            return current if current in tree else root_name

        builder.add_conditional_edges(START, route_entry, {name: name for name in tree})
        return builder.compile(checkpointer=checkpointer)

    async def _build_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Assemble tools via the existing ToolFactory, adapted for LangGraph.

        MCP servers are handled by mcp_client (langchain-mcp-adapters) instead of
        ADK's MCPToolset, and require_confirmation is stripped so ToolFactory does
        not wrap tools in ADK FunctionTool — the LangGraph HITL wrapper is applied
        here instead.
        """
        from shared.utils.tools.tool_factory import get_tool_factory
        from shared.utils.langgraph.tool_adapter import adapt_tools
        from shared.utils.langgraph.mcp_client import create_mcp_tools
        from shared.utils.langgraph.hitl import apply_confirmation_wrapping

        app_name = config.get("name", "unknown")
        factory_config = dict(config)
        mcp_config = factory_config.pop("mcp_servers_config", None)

        tool_config = _json_field(config, "tool_config")
        confirm_names = tool_config.pop("require_confirmation", None) or []
        if not isinstance(confirm_names, list):
            confirm_names = [confirm_names]
        factory_config["tool_config"] = json.dumps(tool_config) if tool_config else None

        raw_tools = get_tool_factory().create_tools(factory_config)
        tools = adapt_tools(raw_tools)
        tools.extend(await create_mcp_tools(mcp_config, agent_name=app_name))
        return apply_confirmation_wrapping(tools, confirm_names, agent_name=app_name)


_agent_builder: Optional[AgentBuilder] = None


def get_agent_builder() -> AgentBuilder:
    global _agent_builder
    if _agent_builder is None:
        _agent_builder = AgentBuilder()
    return _agent_builder
