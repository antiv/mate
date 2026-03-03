"""
Rate limit and budget service.

Enforces per-user, per-agent, per-project limits:
- requests/min (in-memory)
- tokens/hour, tokens/day, tokens/month (from DB)
- max_tokens_per_request (agent only, enforced at callback)

Actions: warn (log), throttle (delay), block (429).
"""

import asyncio
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

import httpx

from .database_client import get_database_client
from .models import RateLimitConfig, AgentConfig
from .token_usage_service import get_token_usage_service

logger = logging.getLogger(__name__)

# In-memory: key -> deque of timestamps (sliding window for requests/min)
_request_timestamps: Dict[str, deque] = {}
_lock = asyncio.Lock()
_sync_lock = threading.Lock()


def _get_redis_client():
    """Optional Redis client for distributed rate limiting."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis.asyncio as redis
        return redis.from_url(redis_url)
    except ImportError:
        logger.debug("redis not installed, using in-memory rate limiting")
        return None


@dataclass
class RateLimitResult:
    allowed: bool
    action: str  # warn, throttle, block
    message: str
    retry_after_seconds: Optional[float] = None


@dataclass
class UsageSnapshot:
    requests_last_min: int
    tokens_last_hour: int
    tokens_last_day: int
    tokens_last_month: int
    tokens_this_request: Optional[int]


def _cleanup_old_timestamps(timestamps: deque, window_seconds: int):
    """Remove timestamps older than window."""
    cutoff = time.monotonic() - window_seconds
    while timestamps and timestamps[0] < cutoff:
        timestamps.popleft()


def _request_count_last_minute_sync(user_id: str, agent_name: Optional[str] = None) -> int:
    """Sync count of requests in last 60s for user (and optionally agent)."""
    count = 0
    prefix = f"user:{user_id}"
    match_key = f"{prefix}:agent:{agent_name}" if agent_name else None
    with _sync_lock:
        for key, ts in list(_request_timestamps.items()):
            if match_key and key == match_key:
                _cleanup_old_timestamps(ts, 60)
                count += len(ts)
            elif not match_key and (key == prefix or key.startswith(prefix + ":agent:")):
                _cleanup_old_timestamps(ts, 60)
                count += len(ts)
    return count


async def _request_count_last_minute(key: str) -> int:
    """Count requests in last 60 seconds (in-memory)."""
    async with _lock:
        if key not in _request_timestamps:
            return 0
        ts = _request_timestamps[key]
        _cleanup_old_timestamps(ts, 60)
        return len(ts)


async def _record_request(key: str):
    """Record a request for rate limiting."""
    async with _lock:
        if key not in _request_timestamps:
            _request_timestamps[key] = deque(maxlen=10000)
        _request_timestamps[key].append(time.monotonic())


def _get_config(session, scope: str, scope_id: str) -> Optional[RateLimitConfig]:
    return session.query(RateLimitConfig).filter(
        RateLimitConfig.scope == scope,
        RateLimitConfig.scope_id == scope_id
    ).first()


def _get_agent_project_id(session, agent_name: str) -> Optional[int]:
    """Get project_id for an agent."""
    agent = session.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
    return agent.project_id if agent else None


class RateLimitService:
    """Service for rate limit checks and config."""

    def __init__(self):
        self.db_client = get_database_client()
        self.token_service = get_token_usage_service()
        self._redis = _get_redis_client()

    def _get_session(self):
        if not self.db_client or not self.db_client.is_connected():
            return None
        return self.db_client.get_session()

    def get_configs(self, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all rate limit configs, optionally filtered by scope."""
        session = self._get_session()
        if not session:
            return []
        try:
            q = session.query(RateLimitConfig)
            if scope:
                q = q.filter(RateLimitConfig.scope == scope)
            return [r.to_dict() for r in q.all()]
        finally:
            session.close()

    def get_config(self, scope: str, scope_id: str) -> Optional[Dict[str, Any]]:
        """Get config for a scope/scope_id."""
        session = self._get_session()
        if not session:
            return None
        try:
            cfg = _get_config(session, scope, scope_id)
            return cfg.to_dict() if cfg else None
        finally:
            session.close()

    def upsert_config(self, scope: str, scope_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Create or update rate limit config."""
        session = self._get_session()
        if not session:
            return None
        try:
            alert_thresholds = kwargs.pop("alert_thresholds", None)
            cfg = _get_config(session, scope, scope_id)
            if cfg:
                for k, v in kwargs.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
                if alert_thresholds is not None:
                    cfg.set_alert_thresholds(alert_thresholds)
            else:
                cfg = RateLimitConfig(scope=scope, scope_id=scope_id, **kwargs)
                if alert_thresholds is not None:
                    cfg.set_alert_thresholds(alert_thresholds)
                session.add(cfg)
            session.commit()
            session.refresh(cfg)
            return cfg.to_dict()
        except Exception as e:
            session.rollback()
            logger.error("Failed to upsert rate limit config: %s", e)
            return None
        finally:
            session.close()

    def delete_config(self, scope: str, scope_id: str) -> bool:
        """Delete rate limit config."""
        session = self._get_session()
        if not session:
            return False
        try:
            cfg = _get_config(session, scope, scope_id)
            if cfg:
                session.delete(cfg)
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error("Failed to delete rate limit config: %s", e)
            return False
        finally:
            session.close()

    def get_usage_snapshot(
        self,
        user_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        project_id: Optional[int] = None,
        tokens_this_request: Optional[int] = None,
    ) -> UsageSnapshot:
        """Get current usage for user/agent/project."""
        now = datetime.now(timezone.utc)
        requests_last_min = _request_count_last_minute_sync(user_id or "", agent_name) if user_id else 0
        tokens_last_hour = 0
        tokens_last_day = 0
        tokens_last_month = 0

        if user_id:
            tokens_last_hour = self.token_service.get_user_tokens_since(
                user_id, now - timedelta(hours=1)
            )
            tokens_last_day = self.token_service.get_user_tokens_since(
                user_id, now - timedelta(days=1)
            )
            tokens_last_month = self.token_service.get_user_tokens_since(
                user_id, now - timedelta(days=30)
            )

        if agent_name:
            tokens_last_day = max(
                tokens_last_day,
                self.token_service.get_agent_tokens_since(
                    agent_name, now - timedelta(days=1)
                ),
            )

        if project_id:
            tokens_last_month = max(
                tokens_last_month,
                self.token_service.get_project_tokens_since(
                    project_id, now - timedelta(days=30)
                ),
            )

        return UsageSnapshot(
            requests_last_min=requests_last_min,
            tokens_last_hour=tokens_last_hour,
            tokens_last_day=tokens_last_day,
            tokens_last_month=tokens_last_month,
            tokens_this_request=tokens_this_request,
        )

    async def check_request_limit(
        self,
        user_id: str,
        agent_name: Optional[str] = None,
        project_id: Optional[int] = None,
        auth_username: Optional[str] = None,
    ) -> Tuple[RateLimitResult, Optional[UsageSnapshot]]:
        """
        Check if request is allowed. Returns (result, usage_snapshot).
        Call _record_request after allowing.
        """
        session = self._get_session()
        if not session:
            return (
                RateLimitResult(allowed=True, action="warn", message="Rate limit check skipped (no DB)"),
                None,
            )

        try:
            # Collect configs for user, agent, project
            configs: List[Tuple[str, str, RateLimitConfig]] = []
            user_cfg = _get_config(session, "user", user_id)
            if user_cfg:
                configs.append(("user", user_id, user_cfg))

            if agent_name:
                agent_cfg = _get_config(session, "agent", agent_name)
                if agent_cfg:
                    configs.append(("agent", agent_name, agent_cfg))

            if project_id is None and agent_name:
                project_id = _get_agent_project_id(session, agent_name)
            if project_id is not None:
                proj_cfg = _get_config(session, "project", str(project_id))
                if proj_cfg:
                    configs.append(("project", str(project_id), proj_cfg))

            # Fallback: rate limit by auth user if no user-specific config
            if not configs and auth_username:
                auth_cfg = _get_config(session, "user", auth_username)
                if auth_cfg:
                    configs.append(("user", auth_username, auth_cfg))

            if not configs:
                return (
                    RateLimitResult(allowed=True, action="warn", message="No rate limits configured"),
                    None,
                )

            # Request key for in-memory tracking
            req_key = f"user:{user_id}"
            if agent_name:
                req_key += f":agent:{agent_name}"

            requests_last_min = await _request_count_last_minute(req_key)

            now = datetime.now(timezone.utc)
            tokens_last_hour = self.token_service.get_user_tokens_since(
                user_id, now - timedelta(hours=1)
            ) if user_id else 0
            tokens_last_day = self.token_service.get_user_tokens_since(
                user_id, now - timedelta(days=1)
            ) if user_id else 0
            tokens_last_month = self.token_service.get_user_tokens_since(
                user_id, now - timedelta(days=30)
            ) if user_id else 0

            if agent_name:
                tokens_last_day = max(
                    tokens_last_day,
                    self.token_service.get_agent_tokens_since(
                        agent_name, now - timedelta(days=1)
                    ),
                )
            if project_id:
                tokens_last_month = max(
                    tokens_last_month,
                    self.token_service.get_project_tokens_since(
                        project_id, now - timedelta(days=30)
                    ),
                )

            usage = UsageSnapshot(
                requests_last_min=requests_last_min,
                tokens_last_hour=tokens_last_hour,
                tokens_last_day=tokens_last_day,
                tokens_last_month=tokens_last_month,
                tokens_this_request=None,
            )

            # Check each config
            for scope, sid, cfg in configs:
                action = cfg.action_on_limit

                # Requests per minute
                if cfg.requests_per_minute is not None:
                    if requests_last_min >= cfg.requests_per_minute:
                        if action == "block":
                            return (
                                RateLimitResult(
                                    allowed=False,
                                    action="block",
                                    message=f"Rate limit exceeded: {requests_last_min} requests per minute (limit: {cfg.requests_per_minute})",
                                    retry_after_seconds=60.0,
                                ),
                                usage,
                            )
                        if action == "throttle":
                            await asyncio.sleep(60.0 / max(1, cfg.requests_per_minute))
                        logger.warning(
                            "[RATE_LIMIT] %s %s: requests/min %s/%s (action=warn)",
                            scope, sid, requests_last_min, cfg.requests_per_minute,
                        )

                # Tokens per hour (user)
                if cfg.tokens_per_hour is not None and scope == "user":
                    if usage.tokens_last_hour >= cfg.tokens_per_hour:
                        if action == "block":
                            return (
                                RateLimitResult(
                                    allowed=False,
                                    action="block",
                                    message=f"Token budget exceeded: {usage.tokens_last_hour} tokens this hour (limit: {cfg.tokens_per_hour})",
                                    retry_after_seconds=3600.0,
                                ),
                                usage,
                            )
                        logger.warning(
                            "[RATE_LIMIT] user %s: tokens/hour %s/%s (action=warn)",
                            sid, usage.tokens_last_hour, cfg.tokens_per_hour,
                        )

                # Tokens per day
                if cfg.tokens_per_day is not None:
                    if usage.tokens_last_day >= cfg.tokens_per_day:
                        if action == "block":
                            return (
                                RateLimitResult(
                                    allowed=False,
                                    action="block",
                                    message=f"Token budget exceeded: {usage.tokens_last_day} tokens today (limit: {cfg.tokens_per_day})",
                                    retry_after_seconds=86400.0,
                                ),
                                usage,
                            )
                        logger.warning(
                            "[RATE_LIMIT] %s %s: tokens/day %s/%s (action=warn)",
                            scope, sid, usage.tokens_last_day, cfg.tokens_per_day,
                        )

                # Tokens per month (project)
                if cfg.tokens_per_month is not None and scope == "project":
                    if usage.tokens_last_month >= cfg.tokens_per_month:
                        if action == "block":
                            return (
                                RateLimitResult(
                                    allowed=False,
                                    action="block",
                                    message=f"Project budget exceeded: {usage.tokens_last_month} tokens this month (limit: {cfg.tokens_per_month})",
                                    retry_after_seconds=86400.0,
                                ),
                                usage,
                            )
                        logger.warning(
                            "[RATE_LIMIT] project %s: tokens/month %s/%s (action=warn)",
                            sid, usage.tokens_last_month, cfg.tokens_per_month,
                        )

            return (
                RateLimitResult(allowed=True, action="warn", message="OK"),
                usage,
            )
        finally:
            session.close()

    async def record_request(self, user_id: str, agent_name: Optional[str] = None):
        """Record a request for rate limiting (call after allowing)."""
        key = f"user:{user_id}"
        if agent_name:
            key += f":agent:{agent_name}"
        await _record_request(key)

    def send_alert_webhook_sync(
        self,
        scope: str,
        scope_id: str,
        threshold_pct: int,
        usage: int,
        limit: int,
        webhook_url: str,
    ):
        """Send budget alert to webhook (sync, for use from callbacks)."""
        try:
            payload = {
                "event": "rate_limit_alert",
                "scope": scope,
                "scope_id": scope_id,
                "threshold_percent": threshold_pct,
                "usage": usage,
                "limit": limit,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with httpx.Client() as client:
                client.post(webhook_url, json=payload, timeout=10.0)
        except Exception as e:
            logger.warning("Failed to send alert webhook: %s", e)

    async def send_alert_webhook(
        self,
        scope: str,
        scope_id: str,
        threshold_pct: int,
        usage: int,
        limit: int,
        webhook_url: str,
    ):
        """Send budget alert to webhook (async)."""
        try:
            payload = {
                "event": "rate_limit_alert",
                "scope": scope,
                "scope_id": scope_id,
                "threshold_percent": threshold_pct,
                "usage": usage,
                "limit": limit,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json=payload, timeout=10.0)
        except Exception as e:
            logger.warning("Failed to send alert webhook: %s", e)


_rate_limit_service: Optional[RateLimitService] = None


def get_rate_limit_service() -> RateLimitService:
    global _rate_limit_service
    if _rate_limit_service is None:
        _rate_limit_service = RateLimitService()
    return _rate_limit_service
