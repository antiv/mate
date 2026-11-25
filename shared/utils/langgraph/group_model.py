"""
A model group on the LangGraph runtime (shared/utils/model_groups.py).

create_react_agent() takes a function as its model, and calls it before each model
call. Here it looks the group's usable models up and gives the first one, with the rest
as its fallbacks for a provider outage, as a fallback model is.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from shared.utils.model_groups import usable_models
from shared.utils.utils import ModelContext

logger = logging.getLogger(__name__)


class GroupChatModel:
    """The models of a group for one agent, and which of them a message came from."""

    def __init__(self, group: str, context: ModelContext, generate_content_config: Optional[Dict[str, Any]]):
        self.group = group
        self._context = context
        self._generate_content_config = generate_content_config
        self._built: Dict[str, Any] = {}
        # litellm's name -> (its name in the group, the group's first model at that call)
        self._called: Dict[str, Tuple[str, str]] = {}

    def _model(self, name: str) -> Any:
        if name not in self._built:
            from shared.utils.langgraph.model_factory import create_chat_model
            self._built[name] = create_chat_model(
                name, generate_content_config=self._generate_content_config, context=self._context)
        return self._built[name]

    def for_tools(self, tools: List[Any]) -> Callable[[Any, Any], Awaitable[Any]]:
        """create_react_agent()'s model, with the agent's tools bound."""
        from shared.callbacks.model_fallback_callback import PROVIDER_OUTAGE_ERRORS
        from langgraph.prebuilt import ToolNode
        from shared.utils.langgraph.model_factory import litellm_model_name
        # As create_react_agent() makes tools of functions before it binds them
        bound = list(ToolNode(tools).tools_by_name.values()) if tools else []

        async def model(state: Any, runtime: Any) -> Any:
            names = await asyncio.to_thread(usable_models, self.group, self._context)
            self._called.update({litellm_model_name(n): (n, names[0]) for n in names})
            first, *rest = [self._model(n).bind_tools(bound) if bound else self._model(n) for n in names]
            return first.with_fallbacks(rest, exceptions_to_handle=PROVIDER_OUTAGE_ERRORS) if rest else first

        return model

    def answered(self, model_version: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """(the model a message came from, the group's first one at that call)."""
        return self._called.get(model_version or "", (model_version, model_version))
