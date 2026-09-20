"""
OpenAI-compatible bridge: MATE agents as chat-completions models.

Beyond plain chat this speaks the tool-calling half of the protocol, so an
external coding agent can hand the model its own tools. The model's call is
emitted as `tool_calls`, the caller runs it locally, and the result comes back
as a `role: "tool"` message that resumes the paused agent turn. The caller's
tools are added to the agent's own — they do not replace them.
"""

import hashlib
import json
import logging
import os
import time
from typing import (Any, AsyncGenerator, Dict, List, NamedTuple, Optional, Set,
                    Tuple, Union)

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from server.openai_translate import (build_runtime_turns, consumed_index,
                                     conversation_key, event_parts, final_chunk,
                                     iter_sse_payloads, normalize_client_tools,
                                     part_function_call, system_text, text_chunk,
                                     tool_call_chunk, transcript_text,
                                     TextDeltaTracker, CLIENT_TOOL_METADATA_KEY,
                                     CONVERSATION_ID_HEADER, has_image_parts)
from server.pat_auth import get_pat_user
from server.widget_routes import model_supports_vision
from shared.utils.database_client import get_database_client
from shared.utils.models import AgentConfig, User
from shared.utils.rate_limit_service import get_rate_limit_service
from shared.utils.utils import get_adk_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["OpenAI Compatibility"])

adk_config = get_adk_config()
ADK_HOST = adk_config.get("adk_host", "localhost")
ADK_PORT = adk_config.get("adk_port", 8001)

# agents_config carries no timestamps, so there is no real creation date to
# report. A constant is at least honest about that, where the row id was not.
_MODELS_CREATED = int(time.time())

_RUN_TIMEOUT_SECONDS = 900.0


class ChatMessage(BaseModel):
    role: str
    # Optional because an assistant message that only makes tool calls carries a
    # null content, and clients replay their own history verbatim.
    content: Optional[Union[str, List[Any]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    tools: Optional[List[Any]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None


@router.get("/models")
async def list_models(user: User = Depends(get_pat_user)):
    """List active root agents that have expose_as_model = True."""
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable"
        )
    try:
        agents = session.query(AgentConfig).filter(
            AgentConfig.disabled.is_(False),
            AgentConfig.expose_as_model.is_(True),
            (AgentConfig.parent_agents.is_(None) |
             (AgentConfig.parent_agents == "") |
             (AgentConfig.parent_agents == "[]"))
        ).all()

        return {
            "object": "list",
            "data": [
                {
                    "id": agent.name,
                    "object": "model",
                    "created": _MODELS_CREATED,
                    "owned_by": "mate",
                }
                for agent in agents
            ],
        }
    finally:
        session.close()


def _load_exposed_agent(agent_name: str) -> Tuple[Optional[int], str]:
    """
    Refuse anything that is not an agent deliberately exposed as a model.

    Returns the agent's project id, which the rate limiter needs, and its model
    name, which says whether an attached screenshot is worth forwarding — both
    of which would otherwise cost a second query.
    """
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        agent = session.query(AgentConfig).filter_by(name=agent_name).first()
        if not agent or agent.disabled or not agent.expose_as_model:
            raise HTTPException(
                status_code=404,
                detail=f"Model/Agent '{agent_name}' not found or not exposed as model"
            )
        return agent.project_id, agent.model_name or ""
    finally:
        session.close()


def _rate_limiting_enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "false").lower() in ("true", "1", "yes")


async def _enforce_rate_limit(user_id: str, agent_name: str,
                              project_id: Optional[int]) -> None:
    """
    Apply the same per-user/agent/project limits the RateLimitMiddleware
    applies to /run_sse. The bridge talks to the runtime directly, so it never
    passes through that middleware and has to ask the service itself.

    One OpenAI request counts as one request against requests_per_minute, even
    when a tool-calling conversation fans out into several runtime turns; token
    budgets are charged from the usage the runtime logs, as everywhere else.
    """
    if not _rate_limiting_enabled():
        return
    svc = get_rate_limit_service()
    result, _ = await svc.check_request_limit(
        user_id=user_id,
        agent_name=agent_name,
        project_id=project_id,
        auth_username=user_id,
    )
    if not result.allowed:
        retry_after = int(result.retry_after_seconds or 60)
        raise HTTPException(
            status_code=429,
            detail=result.message,
            headers={"Retry-After": str(retry_after)},
        )
    await svc.record_request(user_id=user_id, agent_name=agent_name)


def _turn_usage(invocation_ids: List[str]) -> Dict[str, int]:
    """
    Token usage for THIS turn.

    Keyed on the runtime invocation ids seen on the stream, because one turn is
    one invocation and may span several model calls across sub-agents. Summing
    by session id — what this used to do — reports the whole conversation again
    on every request.
    """
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not invocation_ids:
        return usage
    db = get_database_client()
    db_session = db.get_session()
    if not db_session:
        return usage
    try:
        from sqlalchemy import func

        from shared.utils.models import TokenUsageLog
        row = db_session.query(
            func.sum(TokenUsageLog.prompt_tokens),
            func.sum(TokenUsageLog.response_tokens)
        ).filter(TokenUsageLog.request_id.in_(invocation_ids)).first()
        if row:
            usage["prompt_tokens"] = int(row[0] or 0)
            usage["completion_tokens"] = int(row[1] or 0)
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    except Exception as exc:
        logger.warning("Failed to query token usage: %s", exc)
    finally:
        db_session.close()
    return usage


class _SessionState(NamedTuple):
    has_events: bool
    call_ids: Set[str]


async def _session_state(client: httpx.AsyncClient, base: str, agent_name: str,
                         user_id: str, session_id: str) -> Optional[_SessionState]:
    """
    What the runtime already holds for this session, creating it if missing.

    None when the lookup failed: /run_sse can still create the session, and the
    turns are then built as if the runtime held the whole transcript.
    """
    url = f"{base}/apps/{agent_name}/users/{user_id}/sessions/{session_id}"
    try:
        response = await client.get(url)
        if response.status_code == 404:
            await client.post(url, json={})
            return _SessionState(False, set())
        response.raise_for_status()
        events = response.json().get("events") or []
    except Exception as exc:
        logger.warning("Failed to look up ADK session %s: %s", session_id, exc)
        return None
    call_ids = set()
    for event in events:
        for part in event_parts(event):
            function_call = part_function_call(part)
            if function_call and function_call.get("id"):
                call_ids.add(function_call["id"])
    return _SessionState(bool(events), call_ids)


async def _stream_turn(client: httpx.AsyncClient, payload: Dict[str, Any],
                       client_tool_names: set, tracker: TextDeltaTracker,
                       outcome: Dict[str, Any]) -> AsyncGenerator[Tuple[str, Any], None]:
    """
    Run one /run_sse turn, yielding ("text", delta) and ("tool_call", call) as
    the events arrive — the caller must see the answer being written, not a
    silence followed by all of it at once.

    `outcome` is filled in with the invocation id and whether a client tool was
    called; a client tool call ends the turn, because the runtime has stopped
    and is waiting for the caller's result.
    """
    url = f"http://{ADK_HOST}:{ADK_PORT}/run_sse"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    buffer = ""

    async with client.stream("POST", url, json=payload, headers=headers) as response:
        if response.status_code != 200:
            await response.aread()
            raise RuntimeError(f"Agent runtime returned HTTP {response.status_code}")
        async for chunk in response.aiter_bytes():
            buffer += chunk.decode("utf-8", errors="replace")
            payloads, buffer = iter_sse_payloads(buffer)
            for raw in payloads:
                if raw == "[DONE]":
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                error_message = event.get("errorMessage") or event.get("error_message")
                if isinstance(event.get("error"), str):
                    raise RuntimeError(event["error"])
                if error_message:
                    raise RuntimeError(error_message)

                outcome["invocation_id"] = (event.get("invocationId")
                                            or event.get("invocation_id")
                                            or outcome.get("invocation_id"))
                author = event.get("author") or ""

                for part in event_parts(event):
                    function_call = part_function_call(part)
                    if function_call:
                        name = function_call.get("name")
                        if name in client_tool_names:
                            tracker.reset_segment()
                            outcome["called_client_tool"] = True
                            yield "tool_call", {
                                "id": function_call.get("id") or f"call_{name}",
                                "name": name,
                                "args": function_call.get("args") or {},
                            }
                        else:
                            # The agent's own tools run inside MATE; the caller
                            # cannot execute them and must not see them as work
                            # it owes.
                            tracker.reset_segment()
                        continue
                    if part.get("thought"):
                        continue
                    delta = tracker.feed(author, part.get("text") or "",
                                         bool(event.get("partial")))
                    if delta:
                        yield "text", delta


async def _run_completion(agent_name: str, user_id: str, session_id: str,
                          turns: List[Dict[str, Any]],
                          client_tools: List[Dict[str, Any]],
                          completion_id: str) -> AsyncGenerator[Dict[str, Any], None]:
    """Drive the runtime and yield OpenAI chunk dicts."""
    base = f"http://{ADK_HOST}:{ADK_PORT}"
    client_tool_names = {tool["name"] for tool in client_tools}
    tracker = TextDeltaTracker()
    created = int(time.time())

    invocation_ids: List[str] = []
    tool_call_index = 0
    saw_tool_call = False

    async with httpx.AsyncClient(timeout=_RUN_TIMEOUT_SECONDS) as client:
        for turn in turns:
            payload: Dict[str, Any] = {
                "app_name": agent_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": turn,
                "streaming": True,
            }
            if client_tools:
                payload["custom_metadata"] = {CLIENT_TOOL_METADATA_KEY: client_tools}

            outcome: Dict[str, Any] = {}
            async for kind, value in _stream_turn(client, payload, client_tool_names,
                                                  tracker, outcome):
                if kind == "text":
                    yield text_chunk(completion_id, agent_name, created, value)
                else:
                    saw_tool_call = True
                    yield tool_call_chunk(completion_id, agent_name, created,
                                          tool_call_index, value["id"], value["name"],
                                          value["args"])
                    tool_call_index += 1

            if outcome.get("invocation_id"):
                invocation_ids.append(outcome["invocation_id"])
            if outcome.get("called_client_tool"):
                # The runtime is paused on the caller's tool; anything still
                # queued for this request has to wait for the result.
                break

    yield final_chunk(completion_id, agent_name, created,
                      "tool_calls" if saw_tool_call else "stop",
                      _turn_usage(invocation_ids))


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    user: User = Depends(get_pat_user)
):
    """Execute a MATE agent using the OpenAI completions schema."""
    agent_name = body.model
    messages = body.messages

    if not messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")

    project_id, model_name = _load_exposed_agent(agent_name)
    await _enforce_rate_limit(user.user_id, agent_name, project_id)

    client_tools = normalize_client_tools(body.tools, body.tool_choice)
    # A client that tracks its own conversations can say so and keep the agent
    # session across anything it does to the transcript.
    conversation_id = request.headers.get(CONVERSATION_ID_HEADER)
    session_id = conversation_key(agent_name, user.user_id, messages, conversation_id)
    consumed = consumed_index(messages)
    async with httpx.AsyncClient(timeout=10.0) as client:
        session = await _session_state(client, f"http://{ADK_HOST}:{ADK_PORT}",
                                       agent_name, user.user_id, session_id)
    # The caller's system prompt is context for the conversation, not a
    # replacement for the agent's configured instruction, so it rides along with
    # the opening message rather than overriding anything.
    preamble = system_text(messages) if consumed == 0 else ""
    known_call_ids = session.call_ids if session else None
    if session and consumed and not session.has_events:
        # The runtime holds none of this conversation — the client switched
        # models mid-way, or the session is gone — so the history it never saw
        # is replayed as text with the opening turn.
        preamble = "\n\n".join(filter(None, [system_text(messages),
                                              transcript_text(messages[:consumed])]))
    # Only ask whether the model can see when there is something to see: a
    # plain chat turn should not pay for a capability lookup.
    vision = (not has_image_parts(messages, consumed)
              or model_supports_vision(model_name))
    turns = build_runtime_turns(messages, consumed, preamble, vision, known_call_ids)

    if not turns:
        raise HTTPException(
            status_code=400,
            detail="No new user message or tool result to act on"
        )

    completion_id = f"chatcmpl-{hashlib.md5(f'{session_id}_{time.time()}'.encode()).hexdigest()[:12]}"
    chunks = _run_completion(agent_name, user.user_id, session_id, turns,
                             client_tools, completion_id)

    if body.stream:
        async def sse() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in chunks:
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            except Exception as exc:
                logger.error("Error in OpenAI streaming completions: %s", exc)
                error = {"error": {"message": str(exc), "type": "server_error"}}
                yield f"data: {json.dumps(error)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    text = ""
    tool_calls: List[Dict[str, Any]] = []
    finish_reason = "stop"
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        async for chunk in chunks:
            choice = chunk["choices"][0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text += delta["content"]
            for call in delta.get("tool_calls") or []:
                tool_calls.append({
                    "id": call["id"],
                    "type": "function",
                    "function": call["function"],
                })
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
                usage = chunk.get("usage") or usage
    except Exception as exc:
        logger.error("Error in OpenAI completions: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    message: Dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return JSONResponse({
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": agent_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    })
