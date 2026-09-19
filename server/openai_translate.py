"""
Translation between the OpenAI chat-completions wire format and the ADK
/run_sse contract shared by both MATE runtimes.

Kept free of HTTP and database concerns so the hard parts — which messages a
turn still owes the runtime, how a cumulative ADK text stream becomes OpenAI
deltas, and how a client-declared tool call crosses back — are unit testable
on their own.
"""

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Key under RunConfig.custom_metadata carrying the caller's tool declarations.
# ADK forwards custom_metadata verbatim; the LangGraph runtime mirrors it.
CLIENT_TOOL_METADATA_KEY = "mate_client_tools"

SESSION_PREFIX = "openai_sess_"

# Optional request header: a client that tracks its own conversations can pin
# the agent session to one, instead of it being inferred from the transcript.
CONVERSATION_ID_HEADER = "X-MATE-Conversation-Id"


def extract_content_text(content: Any) -> str:
    """Text of an OpenAI message content, which may be a string or a part list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    text_parts.append(part["text"])
                elif "text" in part:
                    text_parts.append(part["text"])
        return "".join(text_parts)
    return str(content) if content is not None else ""


# A screenshot is big, and base64 inflates it by a third. Past this the image
# is not worth the context it would cost, so it is described rather than sent.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _data_uri_blob(url: str) -> Optional[Tuple[str, str]]:
    """(mime_type, base64 payload) of a data: URI, or None if it is not one."""
    if not url.startswith("data:"):
        return None
    header, _, payload = url[len("data:"):].partition(",")
    if not payload or ";base64" not in header:
        return None
    mime = header.split(";", 1)[0] or "image/png"
    return mime, payload


def image_parts(content: Any, vision: bool = True) -> List[Dict[str, Any]]:
    """
    The image parts of an OpenAI message content, as ADK `inline_data`.

    Anything that cannot be sent becomes a text note instead of vanishing: a
    dropped screenshot the model is never told about turns into a conversation
    where it answers about an image it cannot see.

    Only `data:` URIs are accepted. Fetching an http(s) URL the caller supplied
    would make the server issue requests to addresses the caller chooses, which
    is an SSRF the bridge has no reason to offer.
    """
    if not isinstance(content, list):
        return []

    parts: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            continue
        url = (part.get("image_url") or {}).get("url") or ""
        if not vision:
            parts.append({"text": "[image omitted: this agent's model has no vision support]"})
            continue
        blob = _data_uri_blob(url)
        if not blob:
            parts.append({"text": "[image omitted: MATE accepts inline image data, not a URL]"})
            continue
        mime, payload = blob
        # 4 base64 characters carry 3 bytes; close enough to size without decoding.
        if len(payload) * 3 // 4 > MAX_IMAGE_BYTES:
            parts.append({"text": f"[image omitted: larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB]"})
            continue
        parts.append({"inline_data": {"mime_type": mime, "data": payload}})
    return parts


def has_image_parts(messages: List[Any], consumed: int) -> bool:
    """
    Whether the turn this request still owes carries any image.

    Lets the caller skip a model-capability lookup on a plain chat turn, which
    is the overwhelming majority of them.
    """
    for message in messages[consumed:]:
        content = _message_field(message, "content")
        if isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "image_url"
                for part in content):
            return True
    return False


def _message_field(message: Any, name: str, default: Any = None) -> Any:
    """Read a field off either a pydantic message model or a plain dict."""
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def normalize_client_tools(tools: Optional[List[Any]],
                           tool_choice: Any = None) -> List[Dict[str, Any]]:
    """
    OpenAI `tools` + `tool_choice` to the flat declarations MATE puts on the
    wire: [{"name", "description", "parameters"}].

    tool_choice "none" withdraws them all; a named function narrows to that one.
    "auto"/"required" pass everything through — the runtime cannot force a call,
    and silently pretending otherwise would be worse than ignoring the hint.
    """
    if not tools:
        return []

    wanted: Optional[str] = None
    if isinstance(tool_choice, str):
        if tool_choice == "none":
            return []
    elif isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            wanted = (tool_choice.get("function") or {}).get("name")

    declarations: List[Dict[str, Any]] = []
    for tool in tools:
        if isinstance(tool, dict):
            if tool.get("type") not in (None, "function"):
                continue
            function = tool.get("function") or {}
        else:
            function = getattr(tool, "function", None) or {}
            if isinstance(function, dict) is False:
                function = getattr(function, "__dict__", {}) or {}
        name = function.get("name")
        if not name:
            continue
        if wanted and name != wanted:
            continue
        declarations.append({
            "name": name,
            "description": function.get("description") or "",
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return declarations


def system_text(messages: Iterable[Any]) -> str:
    """Concatenated system prompt the client sent, in order."""
    chunks = [
        extract_content_text(_message_field(m, "content"))
        for m in messages
        if _message_field(m, "role") == "system"
    ]
    return "\n\n".join(c for c in chunks if c)


def conversation_key(agent_name: str, user_id: str, messages: List[Any],
                     conversation_id: Optional[str] = None) -> str:
    """
    Stable id for the conversation this request belongs to.

    A client that can name its own conversation should: `conversation_id` (the
    X-MATE-Conversation-Id header) is then the only thing that decides, and the
    session survives anything the client does to the transcript.

    Otherwise the id comes from the agent and the FIRST USER message. Two things
    it deliberately does NOT hash:

    * the first message, which is what this originally used — for a coding agent
      that is a constant system prompt, so every conversation collapsed into one
      session and leaked context between them;
    * the system prompt, which looks discriminating but is the volatile part.
      Cline and Roo rewrite it when you toggle Plan and Act, and clients change
      it on upgrade, so hashing it drops the agent's memory of the conversation
      mid-task.
    """
    if conversation_id:
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
        return f"{SESSION_PREFIX}{user_id}_{digest}"

    first_user = ""
    for message in messages:
        if _message_field(message, "role") == "user":
            first_user = extract_content_text(_message_field(message, "content"))
            break
    seed = "\x00".join([agent_name, first_user])
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{SESSION_PREFIX}{user_id}_{digest}"


def consumed_index(messages: List[Any]) -> int:
    """
    How much of the client's history the runtime already holds.

    An assistant message exists only because the runtime produced it, so
    everything after the last one is what this turn still owes: the results of
    the tool calls it just made, and/or the user's next message. This is why the
    bridge keeps no progress counter of its own — the transcript already says.
    """
    for index in range(len(messages) - 1, -1, -1):
        if _message_field(messages[index], "role") == "assistant":
            return index + 1
    return 0


def build_runtime_turns(messages: List[Any], consumed: int,
                        system_preamble: str = "",
                        vision: bool = True) -> List[Dict[str, Any]]:
    """
    The `new_message` payloads the runtime still owes for this request.

    Returns one or two turns because ADK refuses a message carrying both text
    and function responses (Runner._validate_new_message): pending tool results
    resume the paused invocation, and any new user text starts the next one.
    Assistant messages are not replayed — the runtime already holds them.

    Screenshots a client attaches ride with the user turn as `inline_data`,
    which is what both runtimes already consume. `vision` says whether the
    agent's model can actually look at them.
    """
    pending = messages[consumed:] if consumed < len(messages) else []

    tool_parts: List[Dict[str, Any]] = []
    user_chunks: List[str] = []
    user_images: List[Dict[str, Any]] = []
    for message in pending:
        role = _message_field(message, "role")
        if role == "tool":
            call_id = _message_field(message, "tool_call_id")
            tool_parts.append({
                "function_response": {
                    "id": call_id,
                    "name": _message_field(message, "name") or "client_tool",
                    "response": {"result": extract_content_text(_message_field(message, "content"))},
                }
            })
        elif role == "user":
            content = _message_field(message, "content")
            text = extract_content_text(content)
            if text:
                user_chunks.append(text)
            user_images.extend(image_parts(content, vision))

    turns: List[Dict[str, Any]] = []
    if tool_parts:
        turns.append({"role": "user", "parts": tool_parts})
    if user_chunks or user_images:
        text = "\n\n".join(user_chunks)
        if system_preamble:
            text = f"{system_preamble}\n\n{text}".strip()
        parts: List[Dict[str, Any]] = [{"text": text}] if text else []
        turns.append({"role": "user", "parts": parts + user_images})
    return turns


class TextDeltaTracker:
    """
    Turns an ADK text stream into OpenAI deltas.

    ADK sends partial frames and then a final frame carrying the FULL cumulative
    text of the segment, and resets when a different agent in the tree takes
    over. Emitting frames verbatim therefore duplicates the whole answer.
    """

    def __init__(self) -> None:
        self.last_text = ""
        self.last_author = ""

    def feed(self, author: str, text: str) -> str:
        if author != self.last_author:
            self.last_author = author
            self.last_text = ""
        if not text:
            return ""
        if self.last_text and text.startswith(self.last_text):
            delta = text[len(self.last_text):]
            self.last_text = text
            return delta
        if self.last_text and self.last_text.startswith(text):
            # A shorter repeat of what was already emitted.
            return ""
        self.last_text = text
        return text

    def reset_segment(self) -> None:
        """A tool call ends the current text segment."""
        self.last_text = ""


def iter_sse_payloads(buffer: str) -> Tuple[List[str], str]:
    """Split an SSE read buffer into complete `data:` payloads plus the remainder."""
    payloads = []
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        line = line.rstrip("\r")
        if line.startswith("data: "):
            payloads.append(line[6:])
    return payloads, buffer


def event_parts(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (event.get("content") or {}).get("parts") or []


def part_function_call(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return part.get("functionCall") or part.get("function_call")


def part_function_response(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return part.get("functionResponse") or part.get("function_response")


def text_chunk(completion_id: str, model: str, created: int, delta: str) -> Dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
    }


def tool_call_chunk(completion_id: str, model: str, created: int, index: int,
                    call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    One tool call as a single delta.

    The OpenAI streaming contract lets a call arrive in fragments; sending it
    whole is valid and is all the runtimes can offer, since neither streams
    tool-call arguments token by token.
    """
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {
                "tool_calls": [{
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments or {}, ensure_ascii=False),
                    },
                }]
            },
            "finish_reason": None,
        }],
    }


def final_chunk(completion_id: str, model: str, created: int, finish_reason: str,
                usage: Dict[str, int]) -> Dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        "usage": usage,
    }
