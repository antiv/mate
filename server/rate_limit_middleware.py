"""
Rate limit middleware for auth server.

Intercepts run_sse and widget chat requests, checks limits, returns 429 when blocked.
"""

import json
import logging
from typing import Optional, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from shared.utils.rate_limit_service import get_rate_limit_service, RateLimitResult

logger = logging.getLogger(__name__)


async def _extract_user_agent_from_body(body: bytes) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Parse run_sse JSON body for user_id, app_name (agent), project_id. Supports camelCase and snake_case."""
    try:
        data = json.loads(body.decode("utf-8"))
        user_id = data.get("user_id") or data.get("userId")
        agent_name = data.get("app_name") or data.get("appName")
        project_id = data.get("project_id") or data.get("projectId")
        return (user_id, agent_name, project_id)
    except Exception:
        return (None, None, None)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware that checks rate limits before proxying to ADK."""

    def __init__(self, app, skip_paths: Optional[list] = None):
        super().__init__(app)
        self.skip_paths = set(skip_paths or ["/health", "/login", "/static", "/sw.js", "/docs", "/redoc", "/admin-docs", "/admin-redoc", "/admin-openapi.json"])

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in self.skip_paths):
            return await call_next(request)

        # Only rate limit run_sse and widget chat
        if path != "/run_sse" and not path.startswith("/widget/api/chat"):
            return await call_next(request)

        user_id = "anonymous"
        agent_name = None
        project_id = None
        auth_username = None

        # Get auth user for fallback
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                import base64
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                auth_username = decoded.split(":", 1)[0]
            except Exception:
                pass

        if path == "/run_sse" and request.method == "POST":
            body = await request.body()
            user_id, agent_name, project_id = await _extract_user_agent_from_body(body)
            user_id = user_id or auth_username or "anonymous"
            # Re-create request with body for downstream
            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}
            request = Request(request.scope, receive=receive)
        elif path.startswith("/widget/api/chat") and request.method == "POST":
            body = await request.body()
            try:
                data = json.loads(body.decode("utf-8"))
                user_id = data.get("user_id", "anonymous")
            except Exception:
                user_id = "anonymous"
            agent_name = None
            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}
            request = Request(request.scope, receive=receive)

        svc = get_rate_limit_service()
        logger.info("[RATE_LIMIT] Checking path=%s user_id=%s agent=%s", path, user_id, agent_name)
        result, usage = await svc.check_request_limit(
            user_id=user_id,
            agent_name=agent_name,
            project_id=project_id,
            auth_username=auth_username,
        )

        if not result.allowed:
            retry_after = int(result.retry_after_seconds or 60)
            return Response(
                content=json.dumps({
                    "detail": result.message,
                    "retry_after": retry_after,
                }),
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)

        if result.allowed:
            await svc.record_request(user_id=user_id, agent_name=agent_name)

        return response
