"""
A model group on the ADK runtime (shared/utils/model_groups.py).

create_model("group/<name>") gives a GroupModel: the agent's model for as long as it
runs, which looks up the group's usable models on every call and sends the call to the
first one. Each is built as any agent's model is (create_model), so it takes the
workspace's key on each call.

The next model is tried by the agent's on_model_error callback, as a fallback model is
(callbacks/model_fallback_callback.py). It and the callbacks that record usage, errors
and spans run apart from the call, and find the models of the call in CURRENT_CALL.
"""

import asyncio
import copy
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from google.adk.models.google_llm import Gemini
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models import LlmCapabilities
from pydantic import PrivateAttr

from .model_groups import PREFIX, group_of, usable_models
from .utils import ModelContext


@dataclass
class GroupCall:
    """One model call of an agent on a group: the models it may go to, in order, and the
    one it went to last. Both empty until the group is looked up."""
    group: str
    models: List[str] = field(default_factory=list)
    model: Optional[str] = None


# Set before each model call (token_usage_callback.py), None for an agent on a model
CURRENT_CALL: ContextVar[Optional[GroupCall]] = ContextVar("mate_group_call", default=None)


def answered_by(model_name: Optional[str]) -> Optional[str]:
    """The model the current call went to when model_name is its group, else model_name."""
    call = CURRENT_CALL.get()
    if call is not None and call.model and group_of(model_name) == call.group:
        return call.model
    return model_name


class GroupModel(LiteLlm):
    """The models of a group as one ADK model. A LiteLlm, so ADK keeps the ids that pair
    tool calls with their results, which most providers need."""

    _context: ModelContext = PrivateAttr(default_factory=ModelContext)
    _built: Dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, group: str, context: ModelContext) -> None:
        super().__init__(model=PREFIX + group)
        self._context = context

    @property
    def group(self) -> str:
        return self.model[len(PREFIX):]

    @property
    def capabilities(self) -> LlmCapabilities:
        # Not every model of a group takes an output schema beside tools: ADK then asks
        # for the answer through a tool, which all of them can
        return LlmCapabilities(output_schema_and_tools=False)

    def llm(self, model_name: str) -> Any:
        if model_name not in self._built:
            from .utils import create_model
            self._built[model_name] = create_model(model_name=model_name, context=self._context)
        return self._built[model_name]

    def ask(self, model_name: str, llm_request: LlmRequest, stream: bool) -> AsyncIterator[Any]:
        """The call, sent to this model."""
        llm = self.llm(model_name)
        llm_request.model = llm.model
        if isinstance(llm, Gemini):
            # ADK's own ids were kept for the others; it sends Gemini none
            from google.adk.flows.llm_flows.functions import remove_client_function_call_id
            contents = copy.deepcopy(llm_request.contents)
            for content in contents:
                remove_client_function_call_id(content)
            llm_request = llm_request.model_copy(update={"contents": contents})
        return llm.generate_content_async(llm_request, stream)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncIterator[Any]:
        models = await asyncio.to_thread(usable_models, self.group, self._context)
        call = CURRENT_CALL.get()
        if call is not None and call.group == self.group:
            call.models, call.model = models, models[0]
        async for response in self.ask(models[0], llm_request, stream):
            yield response

    def connect(self, llm_request: LlmRequest) -> Any:
        """A live (voice) connection: the group's first model, when it can hold one."""
        llm = self.llm(usable_models(self.group, self._context)[0])
        llm_request.model = llm.model
        return llm.connect(llm_request)
