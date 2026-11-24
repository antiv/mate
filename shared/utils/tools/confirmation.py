"""
Tools that ask before they run, on ADK: the ones named in an agent's require_confirmation.

A plain function becomes a FunctionTool that asks. A tool object, and each tool of a
toolset (an MCP server's, known only when it is listed), is told to ask. One that can't
be is left out: a tool an administrator said must ask never runs without asking.
"""

import logging
from typing import Any, List, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool

logger = logging.getLogger(__name__)


def _asking(tool: Any, agent_name: str) -> Optional[Any]:
    """The tool, asking; None when it can't."""
    name = getattr(tool, "__name__", None) or getattr(tool, "name", None)
    if not isinstance(tool, BaseTool) and callable(tool):
        logger.info(f"Tool {name} requires user confirmation (agent {agent_name})")
        return FunctionTool(tool, require_confirmation=True)
    # FunctionTool and McpTool: what their require_confirmation parameter sets
    if hasattr(tool, "_require_confirmation"):
        tool._require_confirmation = True
        return tool
    logger.error(f"Tool {name} of agent {agent_name} is left out: it is in require_confirmation, "
                 f"and a {type(tool).__name__} can't ask")
    return None


def _toolset_asking(toolset: BaseToolset, names: List[str], agent_name: str) -> BaseToolset:
    """The toolset, its tools of these names asking each time they are listed."""
    listed = toolset.get_tools

    async def get_tools(*args: Any, **kwargs: Any) -> List[BaseTool]:
        tools = [_asking(tool, agent_name) if tool.name in names else tool
                 for tool in await listed(*args, **kwargs)]
        return [tool for tool in tools if tool is not None]

    toolset.get_tools = get_tools
    return toolset


def apply_confirmation(tools: List[Any], names: List[str], agent_name: str = "unknown") -> List[Any]:
    """The tools, those named asking for confirmation first."""
    if not names:
        return tools
    result = []
    for tool in tools:
        if isinstance(tool, BaseToolset):
            result.append(_toolset_asking(tool, names, agent_name))
            continue
        if (getattr(tool, "__name__", None) or getattr(tool, "name", None)) in names:
            tool = _asking(tool, agent_name)
        if tool is not None:
            result.append(tool)
    return result
