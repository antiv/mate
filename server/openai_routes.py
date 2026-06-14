import json
import time
import httpx
import hashlib
import logging
from typing import List, Optional, Dict, Any, Union
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from shared.utils.database_client import get_database_client
from shared.utils.models import AgentConfig, User
from shared.utils.utils import get_adk_config
from server.pat_auth import get_pat_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["OpenAI Compatibility"])

# Get ADK configuration
adk_config = get_adk_config()
ADK_HOST = adk_config.get("adk_host", "localhost")
ADK_PORT = adk_config.get("adk_port", 8001)

# Helper to extract plain text from OpenAI messages content (which can be a string or a list of blocks)
def extract_content_text(content: Any) -> str:
    """Helper to extract text from simple string or list of content parts."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    text_parts.append(part["text"])
                elif "text" in part:
                    text_parts.append(part["text"])
        return "".join(text_parts)
    return str(content) if content is not None else ""

# Pydantic models for OpenAI request format
class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Any]]

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None

@router.get("/models")
async def list_models(user: User = Depends(get_pat_user)):
    """
    List all active root agents that have expose_as_model = True.
    """
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_530_SITE_IS_FROZEN, # DB error
            detail="Database unavailable"
        )
    try:
        # Query root agents that are exposed and active
        agents = session.query(AgentConfig).filter(
            AgentConfig.disabled.is_(False),
            AgentConfig.expose_as_model.is_(True),
            (AgentConfig.parent_agents.is_(None) | 
             (AgentConfig.parent_agents == "") | 
             (AgentConfig.parent_agents == "[]"))
        ).all()
        
        models_list = []
        for agent in agents:
            models_list.append({
                "id": agent.name,
                "object": "model",
                "created": int(agent.id),  # placeholder creation timestamp or ID
                "owned_by": "mate"
            })
        return {
            "object": "list",
            "data": models_list
        }
    finally:
        session.close()

@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    user: User = Depends(get_pat_user)
):
    """
    Execute a MATE agent using the OpenAI completions schema.
    """
    agent_name = body.model
    messages = body.messages
    
    if not messages:
        raise HTTPException(status_code=400, detail="Messages list cannot be empty")
        
    # Check if agent exists and is exposed
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        agent = session.query(AgentConfig).filter_by(name=agent_name).first()
        if not agent or agent.disabled or not agent.expose_as_model:
            raise HTTPException(
                status_code=404, 
                detail=f"Model/Agent '{agent_name}' not found or not exposed as model"
            )
    finally:
        session.close()
        
    last_msg = extract_content_text(messages[-1].content)
    
    # Generate a deterministic session key based on the first message in this conversation
    # to isolate different chat sessions for the same user & agent.
    first_msg_text = extract_content_text(messages[0].content) if messages else ""
    first_msg_hash = hashlib.md5(first_msg_text.encode("utf-8")).hexdigest()[:12]
    
    # Scoped user & session name for ADK
    scoped_user = user.user_id
    session_id = f"openai_sess_{user.user_id}_{first_msg_hash}"
    
    # Pre-create session on ADK side if it doesn't exist
    async with httpx.AsyncClient() as client:
        # Create session endpoint: POST /apps/{app_name}/users/{user_id}/sessions/{session_id}
        adk_session_url = f"http://{ADK_HOST}:{ADK_PORT}/apps/{agent_name}/users/{scoped_user}/sessions/{session_id}"
        try:
            # Check if session already exists to avoid noisy 409 Conflict logs
            check_resp = await client.get(adk_session_url)
            if check_resp.status_code == 404:
                # Send empty state payload to create session
                await client.post(adk_session_url, json={})
        except Exception as e:
            logger.warning("Failed to check/pre-create session on ADK: %s", e)

    # Payload for ADK run_sse
    adk_payload = {
        "app_name": agent_name,
        "user_id": scoped_user,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [{"text": last_msg}],
        },
        "streaming": True,
    }
    
    target_url = f"http://{ADK_HOST}:{ADK_PORT}/run_sse"
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    
    completion_id = f"chatcmpl-{hashlib.md5(f'{session_id}_{time.time()}'.encode()).hexdigest()[:12]}"

    async def sse_streamer():
        client = httpx.AsyncClient(timeout=900.0)
        try:
            req = client.build_request("POST", target_url, json=adk_payload, headers=headers)
            r = await client.send(req, stream=True)
            
            if r.status_code != 200:
                error_msg = f"ADK server returned error status {r.status_code}"
                logger.error(error_msg)
                yield f"data: {json.dumps({'error': {'message': error_msg}})}\n\n".encode("utf-8")
                return

            last_text = ""
            buffer = ""
            
            async for chunk in r.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw == "[DONE]":
                        break
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                        
                    # Filter out tool calls within MATE agent loop
                    parts = (evt.get("content") or {}).get("parts") or []
                    for part in parts:
                        text = part.get("text")
                        if not text:
                            continue
                            
                        # De-duplicate deltas (similar to frontend widget logic)
                        delta_text = ""
                        if last_text and text.startswith(last_text):
                            delta_text = text[len(last_text):]
                            last_text = text
                        elif last_text and last_text.startswith(text):
                            pass
                        else:
                            delta_text = text
                            last_text += text
                            
                        if delta_text:
                            chunk_data = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": agent_name,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": delta_text},
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(chunk_data)}\n\n".encode("utf-8")
                            
            # Query token usage at the end of the execution
            prompt_tokens = 0
            completion_tokens = 0
            db = get_database_client()
            db_session = db.get_session()
            if db_session:
                try:
                    from sqlalchemy import func
                    from shared.utils.models import TokenUsageLog
                    res = db_session.query(
                        func.sum(TokenUsageLog.prompt_tokens),
                        func.sum(TokenUsageLog.response_tokens)
                    ).filter(TokenUsageLog.session_id == session_id).first()
                    
                    if res and res[0] is not None:
                        prompt_tokens = int(res[0])
                    if res and res[1] is not None:
                        completion_tokens = int(res[1])
                except Exception as db_err:
                    logger.warning("Failed to query token usage: %s", db_err)
                finally:
                    db_session.close()

            # Stream final choice stop with usage
            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": agent_name,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens
                }
            }
            yield f"data: {json.dumps(final_chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        except Exception as e:
            logger.error("Error in OpenAI streaming completions: %s", e)
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n".encode("utf-8")
        finally:
            await r.aclose()
            await client.aclose()

    if body.stream:
        return StreamingResponse(sse_streamer(), media_type="text/event-stream")
    else:
        # For non-streaming, collect all chunks and return a single JSON response
        full_text = ""
        async for chunk in sse_streamer():
            if chunk.startswith(b"data: ") and not chunk.startswith(b"data: [DONE]"):
                try:
                    data = json.loads(chunk[6:].decode("utf-8"))
                    if "error" in data:
                        raise HTTPException(status_code=500, detail=data["error"]["message"])
                    choices = data.get("choices", [])
                    if choices and "content" in choices[0]["delta"]:
                        full_text += choices[0]["delta"]["content"]
                except json.JSONDecodeError:
                    pass
                    
        # Query token usage for the non-streaming final response
        prompt_tokens = 0
        completion_tokens = 0
        db = get_database_client()
        db_session = db.get_session()
        if db_session:
            try:
                from sqlalchemy import func
                from shared.utils.models import TokenUsageLog
                res = db_session.query(
                    func.sum(TokenUsageLog.prompt_tokens),
                    func.sum(TokenUsageLog.response_tokens)
                ).filter(TokenUsageLog.session_id == session_id).first()
                
                if res and res[0] is not None:
                    prompt_tokens = int(res[0])
                if res and res[1] is not None:
                    completion_tokens = int(res[1])
            except Exception as db_err:
                logger.warning("Failed to query token usage for non-streaming: %s", db_err)
            finally:
                db_session.close()

        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": agent_name,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_text
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        })
