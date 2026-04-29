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

# With fire key query param
curl -X POST "https://your-mate.example.com/triggers/{trigger_id}/fire?key=<fire_key>"

# With standard dashboard credentials (bearer token)
curl -X POST https://your-mate.example.com/triggers/{trigger_id}/fire \
  -H "Authorization: Bearer <token>"
```

### Fire key lifecycle

- A **fire key** is generated when you create a webhook trigger. It is shown **once** in a dashboard banner — copy it immediately.
- The raw key is never stored; only its SHA-256 hash is kept in the database.
- To rotate the key: open the trigger edit modal and click **Regenerate Key**. The old key is invalidated immediately.
- If you lose your fire key, regenerate it — there is no recovery.

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
