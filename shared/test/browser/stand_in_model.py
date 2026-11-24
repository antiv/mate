"""A stand-in for OpenRouter: an OpenAI-compatible server with one fixed answer."""

import json
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

ANSWER = "The stand-in model answers."
TITLE = "A named conversation"

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    text = TITLE if "word title" in json.dumps(body["messages"]) else ANSWER
    reply: Dict[str, Any] = {"id": "c1", "created": 0, "model": body.get("model")}
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if not body.get("stream"):
        return JSONResponse({**reply, "object": "chat.completion", "usage": usage, "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})

    async def chunks() -> AsyncIterator[str]:
        for delta, finish in (({"role": "assistant", "content": text}, None), ({}, "stop")):
            chunk = {**reply, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if finish:
                chunk["usage"] = usage
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")
