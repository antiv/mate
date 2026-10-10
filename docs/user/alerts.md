---
title: Alerts
summary: Be told by webhook, Slack, Discord or email when an agent starts failing, keeps falling back to its fallback model, guardrails fire repeatedly, or a token or dollar budget is nearly used up.
audience: user
order: 140
covers:
  - templates/dashboard/alerts.html
  - static/js/alerts-page.js
  - shared/utils/alert_service.py
  - shared/utils/notify.py
  - shared/utils/dashboard/dashboard_server.py::*alert*
---

# Alerts

An alert rule watches one number and notifies you when it crosses a line. Rules are
in **Control Room → Alerts**.

> Rules are evaluated only when your administrator has set `ALERTS_ENABLED=true` on
> the server. They are checked once a minute, so a notification can arrive up to a
> minute after the event.

## What a rule can watch

| Condition | Fires when | You set |
|---|---|---|
| **Agent errors** | Failed requests reach a count within a time window. | **Threshold (count)** and **Window (minutes)** |
| **Guardrail hits** | Guardrail hits reach a count within a time window. | the same |
| **Fallback model used** | Requests answered by an agent's [fallback model](fallback-model.md) reach a count within a time window. | the same |
| **Budget threshold** | Token use or spend in dollars reaches a percentage of a budget. | **Budget in** tokens or US dollars, **Threshold (% of budget)**, the **Period** (hour, day or month; day or month for dollars) and optionally a limit |

A dollar budget counts the [cost](usage-and-audit.md#cost) of priced calls only; calls
whose model has no price add nothing. Its message reads, for example, "agent support
has used 90% of its day budget ($9.00 of $10.00)".

For a budget rule, leave the limit empty to use the budget already configured
for the same scope on the [Rate Limits](rate-limits.md) page, so the number lives in
one place.

A person being refused by an agent's role check is not counted as an agent error.

*Agent errors* cannot tell you whether the [fallback model](fallback-model.md)
covered a failure. On the ADK runtime the primary model's failure counts as an error
even when the fallback then answered; on the LangGraph runtime it does not count at
all. A *Fallback model used* rule counts only the requests the fallback answered, on
both runtimes, so you learn that a provider is down while people are still getting
answers.

## Create a rule

1. Click **New Rule** and give it a name that will make sense in a notification,
   such as "Support bot failing".
2. Choose the **Condition**.
3. Choose the **Scope** and enter its **Scope ID**:

   | Scope | Scope ID | Watches |
   |---|---|---|
   | **Agent** | the agent's name | that agent |
   | **Project** | the project id | every agent in the project |
   | **User** | the user id | that person's requests |
   | **Global** | none | everything |

   Guardrail hits are recorded per agent, so a *Guardrail hits* rule cannot be scoped
   to a user.
4. Choose the **Destination**:
   - **HTTP POST** sends a JSON message to a **Webhook URL**.
   - **Email** sends to a **Recipient**. This needs outgoing mail configured on the
     server.
   - **Slack (incoming webhook)** posts the alert as a message to a channel. In Slack,
     create an [incoming webhook](https://api.slack.com/messaging/webhooks) for the
     channel and paste its URL into **Webhook URL**.
   - **Discord (webhook)** does the same for a Discord channel: in the channel's
     settings, **Integrations → Webhooks → New Webhook**, then **Copy Webhook URL**.

   A Slack or Discord webhook URL lets anyone who has it post in the channel, so the
   rule list shows "Slack webhook" or "Discord webhook" instead of the URL.
5. Set the **Cooldown**, then **Save**.

## How often you are notified

After a rule fires it stays quiet for its **Cooldown**, one hour unless you change
it, even if the condition is still true. A budget rule fires once when the threshold
is crossed in a period, and again in the next period.

If a notification cannot be delivered, the rule's row shows the error, and the
cooldown still applies. A broken webhook is therefore retried at most once per
cooldown.

The list shows when each rule last fired. You can pause a rule without deleting it,
and send a test notification to check the destination.

## Rules worth having

- **Agent errors**, scope Global, 5 in 15 minutes: something is broken, often a
  provider key or an outage.
- **Budget threshold**, 80% of the monthly project budget: time to look before the
  limit blocks people.
- **Guardrail hits**, on the agent behind a public widget: someone may be probing it.
- **Fallback model used**, scope Global, 10 in 15 minutes: a provider is failing and
  the fallback is carrying the load.
