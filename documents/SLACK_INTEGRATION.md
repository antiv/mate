# Slack Integration

Connect a MATE root agent to a Slack workspace so it replies inside Slack when
mentioned. MATE receives Slack events, runs the agent, and posts the answer back
into the same thread.

**Default behaviour is mention-only**: the bot only reacts to `@YourBot …`
messages (and does not read the rest of the channel). See
[Reading whole channels](#reading-whole-channels-optional) to change that.

---

## Overview of the flow

```
@YourBot question   →  Slack Events API  →  POST /integrations/slack/events
                                                   │ verify signature, ack <3s
                                                   ▼
                                          run agent (ADK) → reply
                                                   ▼
                                        chat.postMessage back in thread
```

You need three values from Slack to fill in the MATE form:
**Team ID**, **Bot User OAuth Token** (`xoxb-…`), and **Signing Secret**.

---

## Step 1 — Create the Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
2. Name it (e.g. "MATE Assistant") and pick the workspace.
3. On **Basic Information**, copy the **Signing Secret** (under *App Credentials*).
   This is one of the three values MATE needs.

## Step 2 — Add bot scopes

1. Left sidebar → **OAuth & Permissions**.
2. Under **Scopes → Bot Token Scopes**, add:
   - `app_mentions:read` — receive mention events
   - `chat:write` — post replies
   - *(optional, for DMs)* `im:history`, `im:write`
3. Do **not** add `channels:history` unless you intentionally want full-channel
   reading (see below).

## Step 3 — Install the app & get the bot token

1. Still on **OAuth & Permissions** → **Install to Workspace** → **Allow**.
2. Copy the **Bot User OAuth Token** — it starts with `xoxb-`.
   This is the second value MATE needs.

## Step 4 — Find your Team ID

The Team ID (workspace id, starts with `T…`) is the third value MATE needs.
Easiest ways to get it:

- Open Slack in a browser: the URL contains it, or go to
  **Workspace settings**; or
- Just fire one test mention after Step 6 — MATE logs the incoming `team_id`
  when no matching integration is found (`No active Slack integration for
  team_id=T…`), so you can copy it from the server logs.

## Step 5 — Create the integration in MATE

1. Open the MATE dashboard → **Integrations** (plug icon in the sidebar).
2. Copy the **Slack Event Request URL** shown at the top of the page — it is
   `https://<your-mate-host>/integrations/slack/events`. You'll need it in Step 6.
3. Click **New Integration** and fill in:
   | Field | Value |
   |---|---|
   | Platform | Slack |
   | Project / Root Agent | the agent that should answer |
   | Slack Workspace / Team ID | `T…` from Step 4 |
   | Bot User OAuth Token | `xoxb-…` from Step 3 |
   | Signing Secret | from Step 1 |
   | Mention-only | leave checked (recommended) |
   | Active | checked |
4. **Save**.

> MATE stores the signing secret to verify inbound requests and the bot token to
> post replies. When you later edit the integration, leave the secret fields
> blank to keep the existing values.

## Step 6 — Point Slack at MATE (Event Subscriptions)

1. Slack app → **Event Subscriptions** → toggle **Enable Events** on.
2. **Request URL**: paste the URL from Step 5. Slack sends a verification
   challenge; MATE answers it automatically, so you should see a green
   **Verified ✓**. (MATE must already be running and publicly reachable — see
   [Local development](#local-development).)
3. Under **Subscribe to bot events**, add **`app_mention`**.
   *(Add `message.im` too if you want direct-message support.)*
4. **Save Changes**. If Slack asks you to reinstall the app, do it.

## Step 7 — Test it

1. Invite the bot to a channel: `/invite @YourBot`.
2. Type `@YourBot hello`.
3. The agent replies in a thread under your message.

Each Slack thread maps to its own persistent MATE conversation
(`session_id = slack_<team>_<channel>_<thread>`), and the sender maps to
`user_id = slack_<slack_user_id>` for token tracking and RBAC.

---

## Local development

Slack must reach your MATE server over public HTTPS. For local testing, tunnel
port 8000:

```bash
ngrok http 8000
# or: cloudflared tunnel --url http://localhost:8000
```

Use the tunnel's HTTPS URL as the Request URL in Step 6, e.g.
`https://abcd-1234.ngrok-free.app/integrations/slack/events`.

---

## Reading whole channels (optional)

By default the bot only sees messages that mention it. To let the agent read
**every** message in a channel:

1. Add the `channels:history` bot scope and subscribe to the `message.channels`
   event in Slack, then reinstall the app.
2. Extend the MATE Slack route to handle the `message` event type (the current
   MVP handles `app_mention` only).

> ⚠️ **Privacy / GDPR**: this means the agent receives and stores messages from
> all participants in the channel. Inform members and review your retention
> policy before enabling it. Keep mention-only unless you have a clear reason.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Request URL won't verify | MATE not reachable over HTTPS, or wrong path. URL must end with `/integrations/slack/events`. |
| `No active Slack integration for team_id=T…` in logs | No integration row for that workspace, or it's inactive. Create/enable it with the correct Team ID. |
| Slack signature `401` in logs | Signing Secret in MATE doesn't match the app's. Re-copy it from **Basic Information**. |
| Bot receives events but never replies | Missing `chat:write` scope, or wrong/expired bot token. Reinstall the app and update the token. |
| Bot replies twice | Slack retries on slow acks — MATE de-duplicates by `event_id`, so this should not happen; check for duplicate integrations for the same workspace. |

---

## Related

- `documents/TRIGGERS.md` — one-way autonomous agent runs (not two-way chat)
- `documents/WIDGET_INTEGRATION.md` — embeddable web chat widget
- `documents/OPENAI_COMPATIBILITY.md` — OpenAI-compatible API for coding tools
