"""
Translates a LangGraph message stream into ADK Event JSON frames.

The frontends (workroom, standalone chat, widget) parse ADK's Event schema
directly from SSE. Their text dedupe accepts both pure-delta and cumulative
patterns: partial frames carry text deltas, and each completed AIMessage is
emitted as a final (non-partial) event carrying the full segment text plus
functionCall parts and usage metadata.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, AsyncIterator, Dict, Iterator, Optional, Tuple

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from shared.utils.langgraph.hitl import confirmation_call_id

logger = logging.getLogger(__name__)

TRANSFER_TOOL_NAME = "transfer_to_agent"
CONFIRMATION_CALL_NAME = "adk_request_confirmation"
# The call that carries an output_schema agent's final answer (see agent_builder)
SET_MODEL_RESPONSE_TOOL_NAME = "set_model_response"


def text_of(content: Any) -> str:
    """Extract plain text from a LangChain message content (str or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block.get("text", ""))
        return "".join(chunks)
    return ""


def _new_event(author: str, invocation_id: str) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "author": author,
        "invocationId": invocation_id,
        "timestamp": datetime.now(timezone.utc).timestamp(),
    }


def _usage_of(message: AIMessage) -> Optional[Dict[str, int]]:
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return None
    return {
        "promptTokenCount": usage.get("input_tokens", 0),
        "prompt_token_count": usage.get("input_tokens", 0),
        "candidatesTokenCount": usage.get("output_tokens", 0),
        "candidates_token_count": usage.get("output_tokens", 0),
        "totalTokenCount": usage.get("total_tokens", 0),
        "total_token_count": usage.get("total_tokens", 0),
    }


def ai_message_to_event(message: AIMessage, author: str, invocation_id: str) -> Optional[Dict[str, Any]]:
    """Complete event for a finished AIMessage: text and/or functionCall parts."""
    parts = []
    text = text_of(message.content)
    if text:
        parts.append({"text": text})
    actions: Dict[str, Any] = {}
    for tool_call in (message.tool_calls or []):
        if tool_call.get("name") == SET_MODEL_RESPONSE_TOOL_NAME:
            # Hidden here: the reply is the tool's validated JSON, sent when
            # its result arrives (see translate_stream)
            continue
        parts.append({"functionCall": {
            "id": tool_call.get("id") or str(uuid.uuid4()),
            "name": tool_call.get("name"),
            "args": tool_call.get("args") or {},
        }})
        if tool_call.get("name") == TRANSFER_TOOL_NAME:
            target = (tool_call.get("args") or {}).get("agent_name")
            if target:
                actions["transfer_to_agent"] = target
                actions["transferToAgent"] = target
    if not parts:
        return None
    event = _new_event(author, invocation_id)
    event["content"] = {"role": "model", "parts": parts}
    if actions:
        event["actions"] = actions
    usage = _usage_of(message)
    if usage:
        event["usageMetadata"] = usage
    # The model that answered, as ADK events carry it. With a fallback model it
    # is how the run tells which of the two did.
    model_version = (message.response_metadata or {}).get("model_name")
    if model_version:
        event["modelVersion"] = model_version
    return event


def tool_message_to_event(message: ToolMessage, author: str, invocation_id: str) -> Dict[str, Any]:
    """Complete event for a tool result (functionResponse part)."""
    event = _new_event(author, invocation_id)
    event["content"] = {"role": "user", "parts": [{"functionResponse": {
        "id": getattr(message, "tool_call_id", None) or str(uuid.uuid4()),
        "name": getattr(message, "name", None) or "tool",
        "response": {"result": text_of(message.content)},
    }}]}
    return event


def model_response_events(
    message: ToolMessage, args: Dict[str, Any], author: str, invocation_id: str
) -> Iterator[Dict[str, Any]]:
    """Events for a set_model_response result. As ADK, the reply is the
    validated JSON as text. A failed validation has no reply: the call and its
    error are shown as normal function events."""
    if getattr(message, "status", None) != "error":
        event = _new_event(author, invocation_id)
        event["content"] = {"role": "model", "parts": [{"text": text_of(message.content)}]}
        yield event
        return
    call_id = getattr(message, "tool_call_id", None) or str(uuid.uuid4())
    event = _new_event(author, invocation_id)
    event["content"] = {"role": "model", "parts": [{"functionCall": {
        "id": call_id, "name": SET_MODEL_RESPONSE_TOOL_NAME, "args": args,
    }}]}
    yield event
    yield tool_message_to_event(message, author, invocation_id)


def interrupt_to_event(interrupt_value: Any, author: str, invocation_id: str) -> Dict[str, Any]:
    """HITL: a paused require_confirmation tool → adk_request_confirmation functionCall."""
    payload = interrupt_value if isinstance(interrupt_value, dict) else {"value": interrupt_value}
    original_call = payload.get("originalFunctionCall") or {}
    event = _new_event(author, invocation_id)
    event["content"] = {"role": "model", "parts": [{"functionCall": {
        "id": confirmation_call_id(original_call["id"]) if original_call.get("id") else str(uuid.uuid4()),
        "name": CONFIRMATION_CALL_NAME,
        "args": payload,
    }}]}
    return event


# Inner node names of a compiled react agent; updates from these carry the
# actual messages. Parent-graph node updates (named after sub-agents) repeat
# the same messages at hand-off boundaries and are skipped.
_REACT_NODES = {"agent", "tools", "pre_model_hook", "post_model_hook"}


def _author_from_namespace(namespace: Tuple[str, ...], default: str) -> str:
    """Subgraph namespaces look like ('child_agent:uuid', ...); first segment is the node/agent name."""
    if namespace:
        return namespace[0].split(":")[0] or default
    return default


async def translate_stream(
    stream: AsyncIterator[Tuple[Tuple[str, ...], str, Any]],
    author: str,
    invocation_id: str,
) -> AsyncGenerator[Tuple[Dict[str, Any], bool], None]:
    """Translate an astream(stream_mode=["messages","updates"], subgraphs=True) iterator.

    Yields (event, is_complete) tuples: partial text-delta frames are not
    persisted; complete events (finished AI messages, tool results, interrupts)
    are persisted by the caller. Sub-agent events get the sub-agent's name as
    author (derived from the subgraph namespace).
    """
    set_response_args: Dict[str, Dict[str, Any]] = {}  # call id -> args of hidden calls
    async for namespace, mode, payload in stream:
        event_author = _author_from_namespace(namespace, author)

        if mode == "messages":
            chunk, metadata = payload
            if not isinstance(chunk, AIMessageChunk):
                continue
            delta = text_of(chunk.content)
            if not delta:
                continue
            event = _new_event(event_author, invocation_id)
            event["content"] = {"role": "model", "parts": [{"text": delta}]}
            event["partial"] = True
            yield event, False

        elif mode == "updates":
            if not isinstance(payload, dict):
                continue
            for node, update in payload.items():
                if node == "__interrupt__":
                    for interrupt in (update if isinstance(update, (list, tuple)) else [update]):
                        value = getattr(interrupt, "value", interrupt)
                        yield interrupt_to_event(value, event_author, invocation_id), True
                    continue
                if node not in _REACT_NODES or not isinstance(update, dict):
                    continue
                for message in (update.get("messages") or []):
                    if isinstance(message, ToolMessage):
                        if getattr(message, "name", None) == SET_MODEL_RESPONSE_TOOL_NAME:
                            args = set_response_args.pop(getattr(message, "tool_call_id", None), {})
                            for event in model_response_events(message, args, event_author, invocation_id):
                                yield event, True
                            continue
                        yield tool_message_to_event(message, event_author, invocation_id), True
                    elif isinstance(message, AIMessage):
                        for tool_call in (message.tool_calls or []):
                            if tool_call.get("name") == SET_MODEL_RESPONSE_TOOL_NAME and tool_call.get("id"):
                                set_response_args[tool_call["id"]] = tool_call.get("args") or {}
                        event = ai_message_to_event(message, event_author, invocation_id)
                        if event:
                            yield event, True
