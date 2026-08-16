# Alerts

Rule-based notifications for the three things that mean something is wrong: agents
failing, guardrails firing repeatedly, and token budgets running out.

## Enable

```
ALERTS_ENABLED=true
ALERTS_INTERVAL_SECONDS=60   # optional, default 60, minimum 10
```

Rules are evaluated by a job on the same APScheduler the trigger engine uses, so no extra
process is involved. Nothing is evaluated inside the request path — an alert can therefore
be up to one interval late, which is the trade for keeping model responses free of
database queries and outbound HTTP.

## Conditions

| Condition | Config | Reads |
|---|---|---|
| `agent_error_count` | `{"threshold": 5, "window_minutes": 15}` | `token_usage_logs` rows with `status='ERROR'` |
| `guardrail_count` | `{"threshold": 10, "window_minutes": 60, "guardrail_type": null, "action_taken": null}` | `guardrail_logs` |
| `budget_threshold` | `{"threshold_pct": 90, "period": "day", "token_limit": null}` | token sums vs. the budget |

`period` is `hour`, `day` or `month`. A null `token_limit` resolves the limit from the
matching `rate_limit_config` row for the same scope, so budgets stay configured in one place.

RBAC denials are recorded as `ACCESS_DENIED`, not `ERROR`, so they never trip an error rule.

## Scope

`agent`, `project`, `user`, or `global`, with `scope_id` naming the target (agent name,
project id, user id). Neither `token_usage_logs` nor `guardrail_logs` carries a project id,
so a project-scoped rule is expanded into that project's agent names at evaluation time.

`guardrail_count` cannot be scoped to a user — guardrail hits are recorded per agent.

## Destinations

- `http` — `{"url": "...", "headers": {...}, "timeout": 30}`, POSTs the alert payload as JSON.
- `email` — `{"to": "ops@example.com", "subject": "..."}`, requires `SMTP_HOST` and friends.

Slack is not yet a destination: `channel_integrations` has no channel column and the
existing Slack helper is async and keyed by workspace, not project.

Payload:

```json
{
  "event": "agent_error_alert",
  "rule_id": 3,
  "rule_name": "Support bot failing",
  "condition_type": "agent_error_count",
  "scope": "agent",
  "scope_id": "support_root",
  "value": 7,
  "threshold": 5,
  "window_minutes": 15,
  "message": "agent support_root recorded 7 errors in the last 15 minutes",
  "fired_at": "2026-08-16T12:00:00+00:00"
}
```

Budget alerts keep the historical `"event": "rate_limit_alert"` name so webhooks written
against the old rate-limit alert keep working.

## Cooldown

`cooldown_seconds` (default 3600) is enforced from `alert_rules.last_fired_at` through a
conditional UPDATE. That means it survives a restart and two processes evaluating the same
rule cannot both deliver — only one can move the timestamp out of the window.

Budget rules additionally track which thresholds already fired in the current period, so
crossing 90% today alerts once today and again tomorrow rather than every cooldown until
the period rolls over.

A failed delivery still consumes the cooldown and is recorded in `last_error`, so a dead
endpoint is not retried in a tight loop.

## Dashboard

**Alerts** in the sidebar, under Governance & Access. Each rule shows its last fired time,
fire count and last delivery error. The test button measures the rule now and sends a
clearly-marked test notification without touching the cooldown or the fire count.

## API

- `GET /dashboard/api/alert-rules?scope=&condition_type=`
- `POST /dashboard/api/alert-rules`
- `PUT /dashboard/api/alert-rules/{id}`
- `DELETE /dashboard/api/alert-rules/{id}`
- `POST /dashboard/api/alert-rules/{id}/test`

Everything except the listing is admin-only.
