"""
Shared agent invocation helper for headless callers (chat-platform bridges, etc.).

Sends a single user message to an ADK agent under a caller-supplied, stable
conversation id and returns the final assistant text. Mirrors the collection
logic in trigger_runner._invoke_agent (author-change reset, tool-call filtering,
overlapping-delta de-duplication) but is async and lets the caller pin the
session_id so multi-turn threads persist.
"""

import json
import logging

import httpx

from shared.utils.utils import get_adk_config

logger = logging.getLogger(__name__)


async def _ensure_session(client: httpx.AsyncClient, base: str, agent_name: str,
                          user_id: str, session_id: str) -> None:
    """Create the ADK session at a specific id if it does not already exist."""
    session_url = f"{base}/apps/{agent_name}/users/{user_id}/sessions/{session_id}"
    try:
        check = await client.get(session_url)
        if check.status_code == 404:
            await client.post(session_url, json={})
    except Exception as exc:  # non-fatal: run_sse can still create it
        logger.warning("Failed to check/pre-create ADK session: %s", exc)


async def run_agent_message(agent_name: str, user_id: str, session_id: str,
                            text: str, timeout: float = 120.0) -> str:
    """
    Send `text` to `agent_name` under (user_id, session_id) and return the final
    assistant text. Reuses the ADK /run_sse contract shared by the widget, OpenAI
    and trigger surfaces.
    """
    cfg = get_adk_config()
    base = f"http://{cfg['adk_host']}:{cfg['adk_port']}"

    payload = {
        "app_name": agent_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": text}]},
        "streaming": True,
    }

    last_text = ""
    last_author = ""
    buffer = ""

    async with httpx.AsyncClient(timeout=timeout) as client:
        await _ensure_session(client, base, agent_name, user_id, session_id)

        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        async with client.stream("POST", f"{base}/run_sse", json=payload, headers=headers) as r:
            if r.status_code != 200:
                raise RuntimeError(f"run_sse returned HTTP {r.status_code}")
            async for chunk in r.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].rstrip("\r")
                    if raw == "[DONE]":
                        break
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    author = evt.get("author", "")
                    if author != last_author:
                        last_author = author
                        last_text = ""
                    parts = (evt.get("content") or {}).get("parts") or []
                    has_tool = any(
                        p.get("functionCall") or p.get("functionResponse")
                        or p.get("function_call") or p.get("function_response")
                        for p in parts
                    )
                    if has_tool:
                        last_text = ""
                        continue
                    for part in parts:
                        part_text = part.get("text")
                        if not part_text:
                            continue
                        if last_text and part_text.startswith(last_text):
                            last_text = part_text
                        elif not (last_text and last_text.startswith(part_text)):
                            last_text += part_text

    return last_text.strip()
