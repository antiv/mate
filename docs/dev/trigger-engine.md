---
title: Trigger engine
summary: How triggers are scheduled, authenticated, executed and routed, and where to extend them.
audience: dev
order: 60
covers:
  - shared/utils/trigger_runner.py
  - shared/utils/notify.py
  - shared/utils/models.py::AgentTrigger
  - shared/utils/dashboard/dashboard_server.py::*trigger*
  - shared/utils/dashboard/dashboard_server.py::*system_job*
---

# Trigger engine

For what triggers do from the dashboard, see the [user guide](../user/triggers.md).
This page covers how they work and where the code is.

## Pieces

| Piece | Where | Role |
|---|---|---|
| `AgentTrigger` | `shared/utils/models.py` | One row per trigger. Columns are listed in the [database reference](../reference/database.md). |
| `TriggerRunner` | `shared/utils/trigger_runner.py` | Process singleton (`get_trigger_runner()`). Owns the APScheduler instance, executes triggers, routes output, mints and verifies secrets. |
| Routes | `DashboardServer` in `shared/utils/dashboard/dashboard_server.py` | CRUD under `/dashboard/api/triggers`, plus the public `POST /triggers/{id}/fire`. Listed in the [API reference](../reference/api.md). |
| `post_json`, `send_email` | `shared/utils/notify.py` | Outbound delivery, shared with alert rules. |

The runner starts in the **auth server** process (`initialize_trigger_runner()` in
`auth_server.py`) and in `standalone_server.py`. It does not run inside the agent
server; it calls the agent server over HTTP.

## Execution path

Both entry points end in `_execute_trigger_sync`:

```
cron:     APScheduler thread → _run_trigger_by_id(id) ─┐
webhook:  POST /triggers/{id}/fire → execute_trigger ──┼→ _execute_trigger_sync
test:     POST …/triggers/{id}/test-fire ──────────────┘     ├ render_prompt(prompt, payload)
                                                             ├ _invoke_agent(agent_name, prompt)
                                                             └ _route_output(trigger, response)
```

The result dict is stored in `last_result` and `last_fired_at` is set, whatever the
outcome:

| `status` | Meaning |
|---|---|
| `ok` | Agent answered and output routing did not raise. Carries `agent_response`. |
| `error` | Agent invocation or output routing raised. Carries `message`, and `agent_response` if the agent had already answered. |
| `skipped` | The type is in `UNIMPLEMENTED_TRIGGER_TYPES` (`file_watch`, `event_bus`). |

### Invoking the agent

`_invoke_agent` is a plain HTTP client of the agent server at `ADK_HOST:ADK_PORT`:

1. `POST /apps/{agent}/users/trigger_runner/sessions` creates a fresh session.
2. `POST /run_sse` streams the run. The runner keeps the last text produced by the
   last author, discarding text that preceded a tool call.

Consequences:

- The user id is always `trigger_runner`. Usage is attributed to the `trigger`
  origin (`shared/utils/response_metrics.py`).
- Every firing is a new session, so there is no conversation state between runs.
- It uses synchronous `httpx` because it runs on APScheduler worker threads. A
  webhook or test-fire request therefore blocks until the agent finishes.
- Timeouts are 30 s for session creation and 120 s without data on the stream.
- It depends only on those two endpoints, which the LangGraph runtime also serves
  (`shared/utils/langgraph/api.py`).

### Prompt rendering

`render_prompt` substitutes `{{ payload }}` and `{{ payload.a.b.0 }}`. It is
single-pass (a body containing a placeholder is inserted literally), an unresolved
path renders empty, and each value is capped at `MAX_PAYLOAD_CHARS` (4,000). Cron
firings pass no payload, so the prompt is sent unchanged.

## Scheduling

`start()` creates a `BackgroundScheduler` in UTC with
`coalesce=True, max_instances=1, misfire_grace_time=60` and the default in-memory
job store, then calls `sync_cron_jobs()`.

- `sync_cron_jobs()` reconciles scheduler jobs with the enabled cron rows. It is
  idempotent, and every create, update, toggle and delete route calls it. The job id
  is the trigger id as a string.
- Nothing is persisted in the scheduler, so a run that fell due while the process
  was down is not replayed. A run delayed by more than 60 s is dropped.
- `parse_cron_expression()` turns the five fields into an APScheduler `CronTrigger`
  and raises `ValueError` for an empty expression, the wrong number of fields or a
  field APScheduler rejects. Create and update answer `400` with that reason, so a
  bad expression is not saved. A row saved before this check is logged and skipped.

> **One scheduler per process.** Running the auth server with several uvicorn
> workers starts a scheduler in each, and every cron trigger fires once per worker.
> Run a single worker when cron triggers are in use.

`start()` also registers three system jobs on the same scheduler:

| Job id | Schedule | Enabled by |
|---|---|---|
| `alert_rule_evaluation` | every `ALERTS_INTERVAL_SECONDS` (default 60, minimum 10) | `ALERTS_ENABLED` |
| `wizard_trial_cleanup` | daily 03:00 UTC | `WIZARD_CLEANUP_ENABLED` (default on) |
| `user_cleanup` | daily 03:30 UTC | `CLEANUP_USER_ENABLED` (default on) |

The `/dashboard/api/system-jobs` routes pause and reschedule them in memory only.

## Authenticating a webhook fire

`fire_trigger_webhook` checks, in order:

1. **Fire key**, from the `X-MATE-Trigger-Key` header, else the deprecated `?key=`
   query parameter (logged as a warning, since it ends up in access logs). Compared
   in constant time against `fire_key_hash`, a SHA-256 of the raw key. A wrong key
   is `403`.
2. **Dashboard auth**, only if no key was sent: `require_dashboard_auth` (bearer,
   basic or session). Failure is `403`.
3. **Signature**, only if the row has `require_signature`. Missing or invalid is
   `401`, and a valid fire key alone is then not enough.

### Signature verification

The signature is `HMAC-SHA256(signing_secret, raw_body)`, hex-encoded, in either
header. `X-Hub-Signature-256` is read first.

| Header | Format | Sent by |
|---|---|---|
| `X-Hub-Signature-256` | `sha256=<hex>` | GitHub, GitLab |
| `X-MATE-Signature` | `<hex>` | your own callers |

```bash
BODY='{"key":"MT-32","action":"updated"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SIGNING_SECRET" | awk '{print $2}')

curl -X POST https://your-mate.example.com/triggers/7/fire \
  -H "X-MATE-Trigger-Key: $FIRE_KEY" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

Verification runs over the raw bytes before JSON parsing, so sign exactly what you
send. There is no timestamp or nonce: a captured signed request can be replayed.

### Secrets

| Secret | Format | Stored as | Returned |
|---|---|---|---|
| Fire key | `secrets.token_urlsafe(32)` | SHA-256 hash in `fire_key_hash` | Once, by create and by update with `regenerate_fire_key` |
| Signing secret | `whsec_` + `token_urlsafe(32)` | Clear text in `signing_secret` (HMAC needs the original) | Once, by create and by update with `regenerate_signing_secret`. `to_dict()` exposes only `has_signing_secret`. |

A signing secret is minted for every webhook trigger at creation, so turning
verification on later needs no rotation. A trigger that predates signing gets one
the first time `require_signature` is set.

## Output routing

`_route_output` dispatches on `output_type`, reading `output_config` (JSON):

| `output_type` | Config keys | Behaviour |
|---|---|---|
| `memory_block` | `label`, `description` | `MemoryBlocksService.modify_block`, falling back to `create_block`. Default label `trigger_{id}_output`. Recorded as changed by `trigger:{id}`. |
| `http_callback` | `url`, `headers`, `timeout` (default 30) | POSTs `{"response": "...", "source": "mate_trigger"}`. A non-2xx reply raises, so the run is recorded as `error`. |
| `email` | `to`, `subject` | Plain text over SMTP with STARTTLS, using `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`. A send failure raises. |

An unknown `output_type` logs a warning and does nothing.

## Behaviour worth knowing

These are what the code does today, stated here so nobody has to rediscover them:

- **`is_enabled` is checked by the callers, not by `execute_trigger`.**
  `_run_trigger_by_id` skips a disabled trigger on the cron path, and
  `POST /triggers/{id}/fire` answers `409` for one, after authentication and the
  signature check, so an unauthenticated caller cannot tell whether it is enabled.
  Test-fire calls `execute_trigger` directly and runs a disabled trigger on purpose.
- **Outputs that cannot deliver are refused, then recorded as errors.**
  `output_config_error()` names an `http_callback` without a `url`, and an `email`
  without `to` or on a server without `SMTP_HOST`. Create and update answer `400`
  for these. Update checks only when the request changes the type, the cron
  expression or the output, so rotating a key on an older trigger still works. At
  run time the same check raises, so the run is recorded as `error` with the agent's
  answer kept.
- **`/triggers/{id}/fire` does not check the trigger type.** With dashboard
  credentials it fires a cron trigger too.

## Extending

**A new output destination**: add an `_output_<name>` method and a branch in
`_route_output`, raise on delivery failure so the result is recorded, then add the
option and its fields to `templates/dashboard/modals/trigger_modal.html` and the
config builder in `static/js/triggers-page.js`.

**A new trigger type**: remove it from `UNIMPLEMENTED_TRIGGER_TYPES` (the create and
update routes refuse anything listed there), give it a source that calls
`execute_trigger(trigger_id, payload)`, and enable its option in the modal.

Tests live in `shared/test/`; run `python -m unittest discover -s shared/test -p "test_trigger*.py"`.
