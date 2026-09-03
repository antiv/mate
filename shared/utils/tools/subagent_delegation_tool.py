"""
Dynamic Subagent Delegation Tool for MATE agents.

Allows an agent (typically a root agent) to dynamically spawn, execute, and
aggregate ephemeral subagents in parallel in real time (Fan-out / Fan-in pattern).

Subagents:
- Are ephemeral (in-memory execution via ADK Runner; do not clutter DB).
- Can be equipped with specific tools from ToolFactory.
- Support any model in MATE (Gemini, LiteLLM, OpenRouter, Ollama, OpenAI, Anthropic).
- Have strict depth limits (depth=1: subagents cannot spawn more subagents).
- Have timeouts, error isolation, and token usage logging to token_usage_logs.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import BaseModel, Field

from shared.callbacks.token_usage_callback import (
    capture_model_name_callback,
    log_token_usage_callback,
)
from shared.utils.utils import create_model

logger = logging.getLogger(__name__)

# Canonical map of tool aliases to ToolFactory keys
_CANONICAL_TOOL_MAP: Dict[str, str] = {
    "google_search": "google_search",
    "search": "google_search",
    "web_search": "google_search",
    "browser": "browser",
    "web_browser": "browser",
    "playwright": "browser",
    "code_executor": "code_executor",
    "python": "code_executor",
    "code": "code_executor",
    "bash": "code_executor",
    "terminal": "code_executor",
    "file_search": "file_search",
    "files": "file_search",
    "rag": "file_search",
    "memory_blocks": "memory_blocks",
    "memory": "memory_blocks",
    "image_tools": "image_tools",
    "image": "image_tools",
    "generate_image": "image_tools",
    "google_drive": "google_drive",
    "drive": "google_drive",
    "google_calendar": "google_calendar",
    "calendar": "google_calendar",
    "cv_tools": "cv_tools",
    "custom_functions": "custom_functions",
    "supabase_storage": "supabase_storage",
    "user_profile": "user_profile",
    "image_data_extraction": "image_data_extraction",
    "shop": "shop",
    "mcp": "mcp",
}

# Tools that subagents are strictly forbidden from having (prevent recursion / fork bomb)
_FORBIDDEN_SUBAGENT_TOOLS = frozenset({"subagent_delegation", "delegate_subtasks"})

DEFAULT_MAX_SUBAGENTS = 5
DEFAULT_TIMEOUT_SECONDS = 60.0


class SubtaskSpec(BaseModel):
    """Specification for a dynamic subagent task."""

    name: str = Field(
        ...,
        description="Short alphanumeric identifier for the subtask (e.g. 'research_competitor_a', 'pricing_analysis').",
    )
    instruction: str = Field(
        ...,
        description="Detailed prompt, instructions, and target deliverables for this subagent to execute.",
    )
    role: str = Field(
        default="",
        description="Role or persona of the subagent (e.g. 'Market Research Analyst', 'Python Data Specialist').",
    )
    tools: List[str] = Field(
        default_factory=list,
        description=(
            "List of tool names to equip this subagent with. Examples: "
            "['google_search', 'browser', 'code_executor', 'file_search', 'memory_blocks', 'image_tools']."
        ),
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "Optional model override for this subtask (e.g. 'gemini-2.5-flash', "
            "'openrouter/deepseek/deepseek-chat', 'ollama_chat/llama3.2', 'openai/gpt-4o-mini'). "
            "If omitted, uses configured default or parent model."
        ),
    )


def _get_context_values(tool_context: Optional[ToolContext]) -> Tuple[str, str, str]:
    """Extract app_name, user_id, and session_id from ToolContext."""
    if not tool_context:
        return "unknown", "default", "default"

    app_name = getattr(tool_context, "app_name", None)
    if not app_name and hasattr(tool_context, "_invocation_context") and tool_context._invocation_context:
        app_name = getattr(tool_context._invocation_context, "app_name", None)
    if not app_name:
        app_name = "unknown"

    user_id = None
    if hasattr(tool_context, "_invocation_context") and tool_context._invocation_context:
        user_id = getattr(tool_context._invocation_context, "user_id", None)
    if not user_id:
        user_id = getattr(tool_context, "user_id", None)
    if not user_id and hasattr(tool_context, "session") and tool_context.session:
        user_id = getattr(tool_context.session, "user_id", None)
    if not user_id:
        user_id = "default"

    session_id = None
    if hasattr(tool_context, "_invocation_context") and tool_context._invocation_context:
        if hasattr(tool_context._invocation_context, "session") and tool_context._invocation_context.session:
            session_id = getattr(tool_context._invocation_context.session, "id", None)
    if not session_id:
        session_id = getattr(tool_context, "session_id", None)
    if not session_id:
        session_id = "default"

    return app_name, user_id, session_id


def _build_subagent_tools(
    requested_tool_names: List[str],
    parent_config: Dict[str, Any],
    tool_context: Optional[ToolContext] = None,
) -> Tuple[List[Any], List[str]]:
    """
    Construct tools for a subagent using ToolFactory.
    Strips forbidden delegation tools and inherits parent tool settings if present.
    """
    from .tool_factory import ToolFactory

    tool_factory = ToolFactory()

    # Parse parent tool_config
    parent_tool_config: Dict[str, Any] = {}
    if parent_config and parent_config.get("tool_config"):
        ptc = parent_config.get("tool_config")
        if isinstance(ptc, str):
            try:
                parent_tool_config = json.loads(ptc)
            except Exception:
                parent_tool_config = {}
        elif isinstance(ptc, dict):
            parent_tool_config = ptc

    sub_tool_config: Dict[str, Any] = {}
    has_mcp = False
    assigned_tool_names: List[str] = []

    for raw_name in requested_tool_names:
        if not raw_name or not isinstance(raw_name, str):
            continue
        canon = _CANONICAL_TOOL_MAP.get(raw_name.strip().lower(), raw_name.strip().lower())

        # Fork bomb prevention: subagents CANNOT delegate
        if canon in _FORBIDDEN_SUBAGENT_TOOLS:
            logger.warning(f"Subagent requested forbidden delegation tool '{canon}' — stripped.")
            continue

        if canon == "mcp":
            has_mcp = True
            assigned_tool_names.append("mcp")
            continue

        # Inherit parent configuration dict if available (e.g. memory_blocks, shop, file_search)
        if canon in parent_tool_config and parent_tool_config[canon] is not None:
            sub_tool_config[canon] = parent_tool_config[canon]
        else:
            sub_tool_config[canon] = True

        assigned_tool_names.append(canon)

    subagent_config = {
        "name": f"subagent_{uuid.uuid4().hex[:6]}",
        "project_id": parent_config.get("project_id") if parent_config else None,
        "tool_config": json.dumps(sub_tool_config) if sub_tool_config else None,
        "mcp_servers_config": parent_config.get("mcp_servers_config") if has_mcp else None,
    }

    raw_tools = tool_factory.create_tools(subagent_config)

    # Redundant safety filter
    safe_tools = []
    for t in raw_tools:
        t_name = getattr(t, "__name__", getattr(t, "name", str(t)))
        if t_name not in _FORBIDDEN_SUBAGENT_TOOLS:
            safe_tools.append(t)

    return safe_tools, assigned_tool_names


def _resolve_subagent_model(
    task_model: Optional[str],
    default_model: Optional[str],
    parent_config: Dict[str, Any],
) -> Tuple[Any, str]:
    """
    Resolve model instance and effective model name for a subagent.
    Supports Gemini, OpenRouter, Ollama, OpenAI, Anthropic, DeepSeek, etc.
    """
    model_name = (
        (task_model and task_model.strip())
        or (default_model and default_model.strip())
        or parent_config.get("model_name")
        or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    )

    base_url = None
    api_key = None

    # Inherit endpoint overrides if model matches parent
    if parent_config:
        parent_model = parent_config.get("model_name")
        if not task_model or task_model.strip() == parent_model:
            base_url = parent_config.get("model_base_url")
            api_key = parent_config.get("model_api_key")

    model = create_model(model_name=model_name, api_key=api_key, base_url=base_url)
    return model, model_name


async def _run_single_subagent(
    task: SubtaskSpec,
    parent_app_name: str,
    parent_user_id: str,
    parent_session_id: str,
    parent_config: Dict[str, Any],
    default_model: Optional[str],
    timeout_seconds: float,
    tool_context: Optional[ToolContext],
) -> Dict[str, Any]:
    """Execute a single ephemeral subagent asynchronously with isolated memory and timeout."""
    subagent_id = uuid.uuid4().hex[:6]
    clean_task_name = task.name.strip() or "subtask"
    subagent_name = f"{parent_app_name}_{clean_task_name}_{subagent_id}"
    sub_session_id = f"{parent_session_id}_{clean_task_name}_{subagent_id}"

    # Build tools
    subagent_tools, assigned_tool_names = _build_subagent_tools(
        task.tools, parent_config, tool_context
    )

    # Resolve model
    try:
        model_instance, effective_model_name = _resolve_subagent_model(
            task.model, default_model, parent_config
        )
    except Exception as e:
        logger.error(f"Failed to create model for subagent '{clean_task_name}': {e}")
        return {
            "status": "error",
            "task_name": clean_task_name,
            "role": task.role,
            "model": task.model or default_model or "unknown",
            "tools_assigned": assigned_tool_names,
            "output": None,
            "error": f"Failed to initialize model: {str(e)}",
        }

    # Format instruction with strict anti-looping rules
    role_prefix = f"You are a specialized subagent with role: {task.role}.\n\n" if task.role else ""
    full_instruction = (
        f"{role_prefix}"
        f"Your goal is to solve the following task efficiently using your available tools:\n"
        f"{task.instruction}\n\n"
        "STRICT GUIDELINES:\n"
        "1. Perform at most 1 to 2 focused tool calls or searches to gather relevant data.\n"
        "2. Once you have initial results, immediately synthesize them into a concise, factual response.\n"
        "3. DO NOT loop endlessly, retry repeatedly, or invent nonexistent URLs.\n"
        "4. If a website or tool produces an error, do not retry more than once — proceed directly with your best summary.\n"
    )

    tool_steps = 0
    MAX_TOOL_CALL_STEPS = 3

    def _limit_subagent_tool_steps(tool, args, tool_context):
        nonlocal tool_steps
        tool_steps += 1
        if tool_steps > MAX_TOOL_CALL_STEPS:
            logger.info(
                f"Subagent '{clean_task_name}' reached tool call limit ({MAX_TOOL_CALL_STEPS}). Instructing model to synthesize final answer."
            )
            return {
                "status": "limit_reached",
                "message": (
                    "Tool call limit reached. You have gathered sufficient data. "
                    "Do not call any more tools. Please provide your final response and summary now based on what you have learned so far."
                ),
            }
        return None

    subagent = Agent(
        name=subagent_name,
        model=model_instance,
        description=task.role or clean_task_name,
        instruction=full_instruction,
        tools=subagent_tools,
        before_model_callback=capture_model_name_callback,
        after_model_callback=log_token_usage_callback,
        before_tool_callback=_limit_subagent_tool_steps,
    )

    from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService

    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=subagent.name,
        user_id=parent_user_id,
        session_id=sub_session_id,
    )

    runner = Runner(
        app_name=subagent.name,
        agent=subagent,
        session_service=session_service,
        artifact_service=InMemoryArtifactService(),
    )

    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=task.instruction)],
    )

    async def _execute_loop() -> str:
        final_text = ""
        fallback_text = ""

        async for event in runner.run_async(
            user_id=parent_user_id,
            session_id=sub_session_id,
            new_message=new_message,
        ):
            if event.content and event.content.parts:
                has_tool = bool(event.get_function_calls() or event.get_function_responses())
                if has_tool:
                    continue

                text_parts = [
                    p.text for p in event.content.parts if hasattr(p, "text") and p.text
                ]
                if text_parts:
                    chunk = "".join(text_parts).strip()
                    if event.is_final_response():
                        final_text = chunk
                    elif not fallback_text:
                        fallback_text = chunk

        return final_text or fallback_text

    try:
        response_text = await asyncio.wait_for(_execute_loop(), timeout=timeout_seconds)
        return {
            "status": "success",
            "task_name": clean_task_name,
            "role": task.role,
            "model": effective_model_name,
            "tools_assigned": assigned_tool_names,
            "output": response_text,
            "error": None,
        }
    except asyncio.TimeoutError:
        logger.warning(
            f"Subagent '{clean_task_name}' timed out after {timeout_seconds} seconds."
        )
        return {
            "status": "timeout",
            "task_name": clean_task_name,
            "role": task.role,
            "model": effective_model_name,
            "tools_assigned": assigned_tool_names,
            "output": None,
            "error": f"Subagent execution timed out after {timeout_seconds}s.",
        }
    except Exception as exc:
        logger.error(f"Error in subagent '{clean_task_name}': {exc}", exc_info=True)
        return {
            "status": "error",
            "task_name": clean_task_name,
            "role": task.role,
            "model": effective_model_name,
            "tools_assigned": assigned_tool_names,
            "output": None,
            "error": str(exc),
        }


class SubagentDelegator:
    """Manages subtask delegation and concurrency controls."""

    def __init__(self, parent_config: Dict[str, Any], options: Dict[str, Any]):
        self.parent_config = parent_config or {}
        self.options = options or {}
        self.max_subagents = int(self.options.get("max_subagents", DEFAULT_MAX_SUBAGENTS))
        self.timeout_seconds = min(
            float(self.options.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)), 90.0
        )
        self.default_model = self.options.get("default_model")

    async def delegate_subtasks(
        self,
        tasks: List[SubtaskSpec],
        tool_context: ToolContext = None,
    ) -> Dict[str, Any]:
        """
        Delegate complex, parallel, or specialized subtasks to ephemeral runtime subagents.

        ALWAYS invoke this tool whenever the user asks to research multiple topics or sources in parallel,
        explicitly requests the use of multiple subagents or assistants, or when a complex query can be
        divided into specialized subtasks (e.g. researching different locations, price categories, or sources).
        Each subagent runs concurrently in an isolated context with only the specific tools it requires, and
        returns its distilled findings for you to synthesize.

        Args:
            tasks: List of subtasks to execute concurrently. Each subtask specifies:
                - name: Unique identifier for the subtask (e.g. 'research_competitor_a', 'pricing_analysis')
                - role: Role or persona of the subagent (e.g. 'Market Research Analyst', 'Python Data Specialist')
                - instruction: Clear, specific, detailed instruction and prompt for the subagent to execute
                - tools: List of tool names the subagent needs (e.g. ['google_search', 'browser', 'code_executor', 'file_search', 'memory_blocks', 'image_tools'])
                - model: Optional model override (e.g. 'gemini-2.5-flash', 'openrouter/deepseek/deepseek-chat', 'ollama_chat/llama3.2'). If omitted, uses default/parent model.

        Returns:
            Dictionary containing the execution status and the aggregated results from all subagents.
        """
        # Parse / normalize tasks argument if passed as string or dicts
        raw_tasks = tasks
        if isinstance(raw_tasks, str):
            try:
                raw_tasks = json.loads(raw_tasks)
            except Exception as e:
                return {
                    "status": "error",
                    "error_message": f"Failed to parse tasks argument: {e}",
                    "results": {},
                }

        if isinstance(raw_tasks, dict):
            raw_tasks = [raw_tasks]

        if not raw_tasks or not isinstance(raw_tasks, list):
            return {
                "status": "error",
                "error_message": "No valid subtasks provided. Expected a non-empty list of task objects.",
                "results": {},
            }

        # Convert elements to SubtaskSpec if needed
        validated_tasks: List[SubtaskSpec] = []
        for item in raw_tasks:
            try:
                if isinstance(item, SubtaskSpec):
                    validated_tasks.append(item)
                elif isinstance(item, dict):
                    validated_tasks.append(SubtaskSpec(**item))
                else:
                    logger.warning(f"Unsupported task item type: {type(item)}")
            except Exception as val_err:
                logger.warning(f"Invalid subtask item {item}: {val_err}")

        if not validated_tasks:
            return {
                "status": "error",
                "error_message": "All provided task specifications were invalid.",
                "results": {},
            }

        # Enforce max subagents cap
        if len(validated_tasks) > self.max_subagents:
            logger.warning(
                f"Requested {len(validated_tasks)} subtasks, capping to max_subagents={self.max_subagents}"
            )
            validated_tasks = validated_tasks[: self.max_subagents]

        app_name, user_id, session_id = _get_context_values(tool_context)

        logger.info(
            f"Agent '{app_name}' delegating {len(validated_tasks)} subtasks in parallel (user={user_id}, session={session_id})"
        )
        print(f"\n🚀 [SUBAGENT DELEGATION] Agent '{app_name}' delegating {len(validated_tasks)} subtasks:")
        for t in validated_tasks:
            print(f"   ↳ Subagent '{t.name}' | Role: '{t.role or 'General'}' | Tools: {t.tools} | Model: {t.model or self.default_model or 'inherited'}")

        # Run all subtasks concurrently
        subtask_futures = [
            _run_single_subagent(
                task=task,
                parent_app_name=app_name,
                parent_user_id=user_id,
                parent_session_id=session_id,
                parent_config=self.parent_config,
                default_model=self.default_model,
                timeout_seconds=self.timeout_seconds,
                tool_context=tool_context,
            )
            for task in validated_tasks
        ]

        results = await asyncio.gather(*subtask_futures, return_exceptions=False)

        completed_count = sum(1 for r in results if r.get("status") == "success")
        failed_count = sum(1 for r in results if r.get("status") != "success")

        print(f"✅ [SUBAGENT DELEGATION] Completed {len(validated_tasks)} subtasks: {completed_count} succeeded, {failed_count} failed.\n")

        subtask_results_map = {r["task_name"]: r for r in results}

        return {
            "status": "success",
            "total_tasks": len(validated_tasks),
            "completed_tasks": completed_count,
            "failed_tasks": failed_count,
            "results": subtask_results_map,
        }


def create_subagent_delegation_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create subagent delegation tool from agent config.

    tool_config example:
        {"subagent_delegation": true}
    or with custom options:
        {"subagent_delegation": {
            "max_subagents": 4,
            "timeout_seconds": 90,
            "default_model": "gemini-2.5-flash"
        }}
    """
    tool_config = config.get("tool_config")
    options: Dict[str, Any] = {}

    if isinstance(tool_config, str):
        try:
            tool_config = json.loads(tool_config)
        except Exception:
            tool_config = {}

    if isinstance(tool_config, dict):
        cfg_val = tool_config.get("subagent_delegation")
        if isinstance(cfg_val, dict):
            options = cfg_val
        elif cfg_val is True:
            options = {}
        else:
            return []
    else:
        return []

    delegator = SubagentDelegator(parent_config=config, options=options)
    return [delegator.delegate_subtasks]

