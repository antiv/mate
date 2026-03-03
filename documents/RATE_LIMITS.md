# Rate Limits & Budgets

Per-user, per-agent, and per-project rate limits and token budgets with configurable actions and dashboard alerts.

## Enable

Set in `.env`:

```
RATE_LIMIT_ENABLED=true
```

## Configuration

| Scope   | Scope ID   | Limits |
|---------|------------|--------|
| user    | user_id    | requests/min, tokens/hour, tokens/day |
| agent   | agent_name | tokens/day, max_tokens_per_request |
| project | project_id | tokens/month (monthly budget cap) |

**Actions on limit hit:**
- `warn` – log and continue
- `throttle` – delay request
- `block` – return 429 with `Retry-After` header

**Alert thresholds:** 80%, 90%, 100% (configurable). When crossed, POST to `alert_webhook_url`:

```json
{
  "event": "rate_limit_alert",
  "scope": "user",
  "scope_id": "user123",
  "threshold_percent": 90,
  "usage": 45000,
  "limit": 50000,
  "timestamp": "2025-03-03T12:00:00Z"
}
```

## Dashboard

- **Rate Limits** in sidebar: configure limits, view usage vs limits
- Usage gauges: requests/min, tokens/hour, tokens/day, tokens/month

## API

- `GET /dashboard/api/rate-limits?scope=user` – list configs
- `GET /dashboard/api/rate-limits/usage?user_id=x&agent_name=y&project_id=z` – current usage
- `POST /dashboard/api/rate-limits` – create/update config (JSON body)
- `DELETE /dashboard/api/rate-limits/{scope}/{scope_id}` – delete config

## 429 Response

When blocked:

```json
{
  "detail": "Rate limit exceeded: 65 requests per minute (limit: 60)",
  "retry_after": 60
}
```

Headers: `Retry-After: 60`

## Optional: Redis

For distributed rate limiting across multiple auth server instances:

```
REDIS_URL=redis://localhost:6379
```

Without Redis, request counts use in-memory sliding window (per process).
