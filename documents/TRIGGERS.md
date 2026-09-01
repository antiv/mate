# Trigger Engine

MATE's Trigger Engine lets agents run autonomously — without a human initiating a conversation. Triggers fire an agent with a configured prompt and route its output to a memory block, HTTP endpoint, or email address.

## Trigger Types

| Type | Status | Description |
|------|--------|-------------|
| `cron` | **Live** | APScheduler-backed; fires on any 5-field UTC cron expression |
| `webhook` | **Live** | External systems POST to `/triggers/{id}/fire` with a fire key |
| `file_watch` | Stub | Placeholder — logs "not yet implemented" when fired |
| `event_bus` | Stub | Placeholder — logs "not yet implemented" when fired |

## Cron Triggers

Cron expressions follow standard 5-field syntax (all times in UTC):

```
minute  hour  day  month  weekday
  0      9     *     *     1-5      → weekdays at 09:00 UTC
  */15   *     *     *     *        → every 15 minutes
  0      0     1     *     *        → 1st of every month at midnight
```

The scheduler uses APScheduler's `BackgroundScheduler` with `coalesce=True` — missed firings are collapsed into one, so you never get a burst of catch-up runs after downtime.

> **Multi-worker caution**: TriggerRunner is per-process. Running MATE with multiple uvicorn workers (e.g. `--workers 4`) will start a scheduler in each worker, causing duplicate cron firings. Use single-worker mode (`--workers 1` or default) when cron triggers are in use.

## Webhook Triggers

### Firing a webhook trigger

```bash
# With fire key header (recommended for external callers)
curl -X POST https://your-mate.example.com/triggers/{trigger_id}/fire \
  -H "X-MATE-Trigger-Key: <fire_key>"

# With fire key query param (deprecated — see below)
curl -X POST "https://your-mate.example.com/triggers/{trigger_id}/fire?key=<fire_key>"

# With standard dashboard credentials (bearer token)
curl -X POST https://your-mate.example.com/triggers/{trigger_id}/fire \
  -H "Authorization: Bearer <token>"
```

> **`?key=` is deprecated.** A secret in the URL lands in access logs, proxy logs and browser
> history, where it outlives any rotation you meant it to have. It still works and fires a
> warning in the server log; use the `X-MATE-Trigger-Key` header, or sign the request and drop
> the query param entirely.

### Using the request body in the prompt

The JSON body of the firing request is available to the trigger's prompt through
`{{ payload }}` placeholders, so a webhook can say *what happened* instead of only *that
something happened*.

| Placeholder | Renders |
|---|---|
| `{{ payload }}` | the whole body as JSON |
| `{{ payload.key }}` | one top-level field |
| `{{ payload.issue.fields.summary }}` | a nested field, by dotted path |
| `{{ payload.commits.0.id }}` | a list element, by index |

```bash
curl -X POST https://your-mate.example.com/triggers/7/fire \
  -H "X-MATE-Trigger-Key: <fire_key>" \
  -H "Content-Type: application/json" \
  -d '{"key": "MT-32", "action": "updated"}'
```

With the prompt `Issue {{ payload.key }} was {{ payload.action }} — summarise it`, the agent
receives `Issue MT-32 was updated — summarise it`.

Notes:

- A prompt with no placeholder is sent unchanged, so existing triggers behave exactly as
  before. A body that is missing or is not JSON is not an error — the trigger fires with no
  payload and placeholders are left as-is.
- An unresolved path renders empty rather than failing, so one missing field does not stop
  the run.
- Each substituted value is capped at 4,000 characters and truncated with a marker beyond
  that, so an oversized POST cannot inflate the prompt or the token bill.
- Substitution is single-pass: a body that itself contains `{{ payload.x }}` is inserted as
  literal text, not re-expanded.
- `POST /dashboard/api/triggers/{id}/test-fire` accepts the same JSON body, so you can
  exercise a payload-using prompt from the dashboard before wiring up the real caller.

> **Treat the body as untrusted input.** Whoever holds the fire key controls text that goes
> straight into an agent prompt. Enable the `prompt_injection` guardrail on any agent whose
> trigger interpolates a payload, and keep the trigger's output destination narrow.

### Verifying signatures (optional)

A fire key authenticates the *caller*; it cannot prove who composed the *body*. Anyone who has
ever seen the key — a proxy log, a CI variable, a screenshot of the one-time banner — can forge
any payload, and since the body reaches the agent's prompt, that is a prompt-injection surface
on an endpoint designed to be called by third parties. A signature binds the body to the sender;
a shared key does not. This is why GitHub, GitLab, Jira and Stripe all sign.

Turn it on per trigger: open the trigger edit modal and tick **Require signed requests**, or
`PUT /dashboard/api/triggers/{id}` with `{"require_signature": true}`. It is **off by default**,
so existing callers keep working unchanged.

Each webhook trigger gets a **signing secret** alongside its fire key, shown once in the same
dashboard banner. To rotate it, click **Regenerate Signing Secret** — senders on the old secret
start failing immediately.

The signature is `HMAC-SHA256(signing_secret, raw_request_body)`, hex-encoded, sent in either
header:

| Header | Format | Sent by |
|---|---|---|
| `X-Hub-Signature-256` | `sha256=<hex>` | GitHub, GitLab |
| `X-MATE-Signature` | `<hex>` | MATE-native callers |

`X-Hub-Signature-256` is checked first. Verification runs over the raw bytes, **before** the body
is parsed as JSON, and the comparison is constant-time.

```bash
BODY='{"key":"MT-32","action":"updated"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SIGNING_SECRET" | awk '{print $2}')

curl -X POST https://your-mate.example.com/triggers/7/fire \
  -H "X-MATE-Trigger-Key: $FIRE_KEY" \
  -H "X-Hub-Signature-256: sha256=$SIG" \
  -H "Content-Type: application/json" \
  -d "$BODY"
```

For GitHub or GitLab, paste the signing secret into the webhook's **Secret** field — they compute
`X-Hub-Signature-256` themselves.

Notes:

- When verification is required, a missing or wrong signature is rejected **401**, and a valid
  fire key on its own is not sufficient.
- Triggers without it enabled behave exactly as before — no signature is looked for.
- Sign the exact bytes you send. Re-serialising parsed JSON reorders keys and changes whitespace,
  which changes the digest.
- **No replay protection.** A captured signed request can be replayed, since there is no
  timestamp or nonce window. Keep the trigger's output destination narrow, and treat a signed
  body as authentic, not as fresh.

### Fire key lifecycle

- A **fire key** is generated when you create a webhook trigger. It is shown **once** in a dashboard banner — copy it immediately.
- The raw key is never stored; only its SHA-256 hash is kept in the database.
- To rotate the key: open the trigger edit modal and click **Regenerate Key**. The old key is invalidated immediately.
- If you lose your fire key, regenerate it — there is no recovery.
- The **signing secret** is separate and rotates separately. Unlike the fire key it is stored in
  the clear, because verifying a signature means recomputing it and a hash could not.

## Output Destinations

### `memory_block`

Writes the agent's response to a memory block in the same project. The block is created if it doesn't exist.

Config:
```json
{ "label": "daily_report_output" }
```

If `label` is omitted, the block is named `trigger_{id}_output`.

### `http_callback`

POSTs the agent's response as JSON to any URL.

Config:
```json
{
  "url": "https://your-service.example.com/webhook",
  "headers": { "Authorization": "Bearer token" },
  "timeout": 30
}
```

Payload sent:
```json
{ "response": "<agent response text>", "source": "mate_trigger" }
```

### `email`

Sends the agent's response via SMTP. Requires environment variables:

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `SMTP_FROM` | From address (defaults to `SMTP_USER`) |

Config:
```json
{ "to": "user@example.com", "subject": "Daily Report" }
```

## Dashboard

Navigate to **Triggers** in the sidebar (⚡ bolt icon).

- **Create**: Click "New Trigger", fill in the form, choose output destination
- **Enable/Disable**: Toggle switch in the table row — cron jobs are added/removed from the scheduler immediately
- **Test Fire**: Run button (▶) fires the trigger immediately and shows the result
- **Edit**: Pencil icon — edit any field; for webhook triggers, use "Regenerate Key" if needed
- **Delete**: Trash icon — removes the trigger and its scheduler job

## Export / Import

Triggers are included in the agent export JSON:

```json
{
  "agents": [...],
  "memory_blocks": [...],
  "triggers": [
    {
      "name": "Daily Report",
      "trigger_type": "cron",
      "cron_expression": "0 9 * * 1-5",
      "agent_name": "reporter_agent",
      "prompt": "Generate today's summary report",
      "output_type": "memory_block",
      "output_config": { "label": "daily_report" }
    }
  ]
}
```

**Import behaviour**: imported triggers always start **disabled** with no `webhook_path` or `fire_key`. You must enable them and (for webhook type) regenerate a fire key before they become active. This is intentional — carrying over webhook paths from another deployment would break authentication.

## Standalone Binary

Triggers are bundled into the standalone SQLite database during `build_standalone_agent.py`. The standalone server starts the TriggerRunner at launch, so cron triggers fire automatically.

Webhook triggers in standalone mode work the same way — fire `POST /triggers/{id}/fire` against the standalone server's port.

## Environment Variables Reference

| Variable | Purpose |
|----------|---------|
| `SMTP_HOST` | SMTP server for email output |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `SMTP_USER` | SMTP login username |
| `SMTP_PASS` | SMTP login password |
| `SMTP_FROM` | From address for outgoing email |
| `ADK_HOST` | ADK server host (default: `127.0.0.1`) — used by TriggerRunner to invoke agents |
| `ADK_PORT` | ADK server port (default: `8001`) — used by TriggerRunner to invoke agents |
