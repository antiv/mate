"""
Tools the CALLER executes, declared per request.

An external coding agent (OpenCode, Continue, Cline) reaching MATE over the
OpenAI-compatible bridge brings its own tools — read, write, edit, bash — that
only it can run. They arrive on RunConfig.custom_metadata and are surfaced here
as ADK long-running tools: the model may call them, the runtime emits the call
and stops instead of executing it, and the caller returns the result as a
function response, which resumes the invocation.

Resolved through a toolset rather than baked into the agent because
BaseToolset.get_tools() is re-evaluated on every LLM step, so a per-request tool
set needs no agent rebuild and no cache invalidation.
"""

import logging
from typing import Any, Dict, List, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.genai import types

logger = logging.getLogger(__name__)

# Must match server.openai_translate.CLIENT_TOOL_METADATA_KEY.
CLIENT_TOOL_METADATA_KEY = "mate_client_tools"

_JSON_TYPE_TO_SCHEMA_TYPE = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
    "null": "NULL",
}


def json_schema_to_schema(schema: Any) -> types.Schema:
    """
    JSON Schema as sent by an OpenAI client to the google.genai Schema the model
    declaration needs.

    Unknown keywords are dropped rather than passed through: a Schema that fails
    to construct takes the whole tool declaration with it, and a tool the model
    cannot see at all is worse than one with a loose parameter description.
    """
    if not isinstance(schema, dict):
        return types.Schema(type="OBJECT")

    # anyOf/oneOf carry no type of their own.
    variants = schema.get("anyOf") or schema.get("oneOf")
    if variants and isinstance(variants, list):
        converted = [json_schema_to_schema(v) for v in variants if isinstance(v, dict)]
        if converted:
            return types.Schema(any_of=converted,
                                description=schema.get("description") or None)

    raw_type = schema.get("type")
    nullable = None
    if isinstance(raw_type, list):
        # ["string", "null"] is how many generators spell an optional field.
        non_null = [t for t in raw_type if t != "null"]
        nullable = len(non_null) < len(raw_type) or None
        raw_type = non_null[0] if non_null else "null"
    schema_type = _JSON_TYPE_TO_SCHEMA_TYPE.get(raw_type or "object", "OBJECT")

    kwargs: Dict[str, Any] = {"type": schema_type}
    if schema.get("description"):
        kwargs["description"] = schema["description"]
    if nullable:
        kwargs["nullable"] = True

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        # Gemini only accepts string enums; anything else keeps the bare type.
        if all(isinstance(v, str) for v in enum_values):
            kwargs["enum"] = enum_values

    if schema_type == "OBJECT":
        properties = schema.get("properties")
        if isinstance(properties, dict) and properties:
            kwargs["properties"] = {
                key: json_schema_to_schema(value)
                for key, value in properties.items()
                if isinstance(value, dict)
            }
        required = schema.get("required")
        if isinstance(required, list) and required:
            kwargs["required"] = [r for r in required if isinstance(r, str)]
    elif schema_type == "ARRAY":
        items = schema.get("items")
        kwargs["items"] = json_schema_to_schema(items) if isinstance(items, dict) \
            else types.Schema(type="STRING")

    try:
        return types.Schema(**kwargs)
    except Exception as exc:
        logger.warning("Could not build a schema for a client tool parameter (%s); "
                       "falling back to an untyped object", exc)
        return types.Schema(type="OBJECT")


def _client_tool_stub(**kwargs: Any) -> None:
    """
    Body of every client tool.

    Returning None is what makes ADK treat the call as outstanding: it emits the
    function call with the tool's id in longRunningToolIds, declines to
    synthesise a response, and ends the invocation.
    """
    return None


class ClientTool(LongRunningFunctionTool):
    """A single caller-executed tool, declared from the caller's JSON Schema."""

    def __init__(self, name: str, description: str, parameters: Any) -> None:
        stub = _client_tool_stub
        super().__init__(func=stub)
        # FunctionTool takes its identity from the wrapped function; these tools
        # all share one stub, so name and declaration are set explicitly.
        self.name = name
        self.description = description or f"Tool '{name}' executed by the calling client."
        self._client_parameters = parameters

    def _get_declaration(self) -> Optional[types.FunctionDeclaration]:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=json_schema_to_schema(self._client_parameters),
        )


class ClientToolset(BaseToolset):
    """
    Surfaces the tools carried on RunConfig.custom_metadata for this request.

    Inert for every other caller: without the metadata key it contributes
    nothing, so attaching it to an agent does not change existing behaviour.
    """

    def __init__(self, reserved_names: Optional[Any] = None) -> None:
        super().__init__()
        # The agent's own tools win a name collision — the operator configured
        # those deliberately, and the agent's instruction may depend on them.
        self._reserved_names = frozenset(reserved_names or ())

    async def get_tools(self, readonly_context: Any = None) -> List[BaseTool]:
        declarations = self._declarations(readonly_context)
        tools: List[BaseTool] = []
        for declaration in declarations:
            name = declaration.get("name")
            if not name:
                continue
            if name in self._reserved_names:
                logger.warning("Client tool '%s' collides with a tool the agent already "
                               "has; keeping the agent's own tool", name)
                continue
            tools.append(ClientTool(
                name=name,
                description=declaration.get("description") or "",
                parameters=declaration.get("parameters"),
            ))
        return tools

    async def close(self) -> None:
        return None

    @staticmethod
    def _declarations(readonly_context: Any) -> List[Dict[str, Any]]:
        run_config = getattr(readonly_context, "run_config", None)
        metadata = getattr(run_config, "custom_metadata", None)
        if not isinstance(metadata, dict):
            return []
        declarations = metadata.get(CLIENT_TOOL_METADATA_KEY)
        if not isinstance(declarations, list):
            return []
        return [d for d in declarations if isinstance(d, dict)]
