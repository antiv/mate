---
title: Triggers
summary: Run an agent on a schedule or from a webhook and send the result somewhere.
audience: user
order: 60
covers:
  - templates/dashboard/triggers.html
  - templates/dashboard/modals/trigger_modal.html
  - static/js/triggers-page.js
  - shared/utils/trigger_runner.py
  - shared/utils/dashboard/dashboard_server.py::*trigger*
  - shared/utils/dashboard/dashboard_server.py::*system_job*
---

# Triggers

A trigger runs an agent without anyone opening a chat. It sends the agent a prompt
you wrote in advance, then delivers the answer to a memory block, a URL or an email
address.

Use one when the work should happen on its own: a report every weekday morning, a
summary each time your issue tracker reports a change, a nightly check that writes
its findings where another agent will read them.

Triggers are on the **Triggers** page in the sidebar. The page is for admins only.

## Create a trigger

Click **New Trigger** and fill in:

| Field | What to enter |
|---|---|
| **Name** | A label for the list. |
| **Type** | **Cron (schedule)** or **Webhook (HTTP POST)**. File Watch and Event Bus are listed but not implemented and cannot be selected. |
| **Project**, **Agent** | The agent to run. Choose the project first; the agent list follows it. |
| **Prompt** | The message the agent receives each time the trigger fires. |
| **Output Destination** | Where the answer goes. See [Where the answer goes](#where-the-answer-goes). |

Every run starts a new conversation. The agent does not remember earlier runs, so
the prompt has to carry everything it needs, or the agent has to read it from a
memory block or a tool.

## Run on a schedule

Choose **Cron (schedule)** and enter a five-field cron expression. All times are
**UTC**, not your local time.

```
minute  hour  day  month  weekday

0 9 * * 1-5      weekdays at 09:00 UTC
*/15 * * * *     every 15 minutes
0 0 1 * *        the first of each month at midnight UTC
```

Things to know:

- If the server is down at the scheduled time, that run is skipped. It is not made
  up when the server comes back.
- If the previous run of the same trigger is still going when the next one is due,
  the next one is skipped.
- An expression that does not have exactly five fields is ignored. The trigger
  saves but never fires, so check **Last Run** after the first scheduled time.

## Run from a webhook

Choose **Webhook (HTTP POST)**. After you save, a banner shows two secrets:

- the **fire key**, which the caller sends to prove it may fire the trigger
- the **signing secret**, used only if you turn on signed requests

**Copy both immediately.** They are shown once, and the banner disappears after a
minute. If you lose one, open the trigger and use **Regenerate Key** or
**Regenerate Signing Secret**. The old value stops working at once.

The caller then sends a POST to the trigger's URL with the key in a header:

```bash
curl -X POST https://your-mate.example.com/triggers/7/fire \
  -H "X-MATE-Trigger-Key: <fire key>"
```

The request waits until the agent has finished and returns the agent's answer, so
the caller should allow for a long response time.

### Use the request body in the prompt

If the caller sends JSON, the prompt can use it through `{{ payload }}` placeholders:

| In the prompt | Becomes |
|---|---|
| `{{ payload }}` | the whole body |
| `{{ payload.key }}` | one field |
| `{{ payload.issue.fields.summary }}` | a nested field |
| `{{ payload.commits.0.id }}` | an item in a list, counted from 0 |

With the prompt `Issue {{ payload.key }} was {{ payload.action }}. Summarise it.` and
the body `{"key": "MT-32", "action": "updated"}`, the agent receives
`Issue MT-32 was updated. Summarise it.`

A field that is missing becomes empty text rather than an error. Each value is cut
off at 4,000 characters.

Whoever holds the fire key decides what text reaches your agent. Turn on the
prompt-injection guardrail for an agent whose trigger uses the payload, and choose a
narrow output destination.

### Require signed requests

A fire key shows that the caller is allowed to fire the trigger. It does not show
that the body is the one the sender wrote. Tick **Require signed requests** to make
MATE reject any request whose body was not signed with the signing secret.

For GitHub and GitLab, paste the signing secret into the webhook's **Secret** field
and they sign each request themselves. For your own callers, see the
[trigger engine guide](../dev/trigger-engine.md#signature-verification).

## Where the answer goes

| Destination | What happens | You provide |
|---|---|---|
| **Memory Block** | The answer replaces the contents of a memory block in the same project. The block is created if it does not exist. | A label. Left empty, the block is named `trigger_<id>_output`. |
| **HTTP Callback** | The answer is sent as JSON to a URL. | The URL, and optional headers as JSON. |
| **Email** | The answer is emailed as plain text. | The recipient and an optional subject. Your administrator must have configured outgoing mail on the server. |

A memory block destination is how one agent hands work to another: a trigger writes
the block overnight, and an agent with memory blocks enabled reads it the next day.

## Test, pause, change, delete

Each row in the list has:

- **Test fire** (▶) runs the trigger now and shows the result. It works whether the
  trigger is enabled or not.
- The **Enabled** switch pauses and resumes a scheduled trigger immediately.
- The pencil opens the trigger for editing; the bin deletes it.

**Last Run** shows when the trigger last fired and whether it succeeded. Token usage
from triggers appears on the **Usage** page under the *trigger* origin.

> **Switching a webhook trigger off does not block its URL.** The switch stops
> schedules only. A webhook trigger that is switched off still runs when called with
> a valid fire key. To stop one, regenerate its key or delete the trigger.

## System background tasks

Below your triggers, the page lists maintenance jobs that MATE runs itself, such as
removing expired trial agents and inactive temporary users. You can pause one or
change its schedule, but the change lasts only until the server restarts.

## Moving triggers between installations

Triggers are included when you export agents. After an import they are switched off
and webhook triggers have no key: enable each one, and regenerate the key for
webhook triggers, before relying on them.
