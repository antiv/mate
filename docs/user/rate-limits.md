---
title: Rate limits and budgets
summary: Cap how many requests and tokens a user, an agent or a project may use, and choose what happens at the cap.
audience: user
order: 100
covers:
  - templates/dashboard/rate_limits.html
  - shared/utils/rate_limit_service.py
  - server/rate_limit_middleware.py
  - shared/utils/dashboard/dashboard_server.py::*rate_limit*
---

# Rate limits and budgets

Every message to an agent costs tokens, and on a public widget anyone can send
them. Limits put a ceiling on that. They are set in
**Control Room → Rate Limits**.

> Limits do nothing until your administrator sets `RATE_LIMIT_ENABLED=true` on the
> server. You can create configurations before that; they are simply not applied.

## What can be limited

A limit is attached to a **scope**: one user, one agent or one project.

| Scope | Scope ID | Limits you can set |
|---|---|---|
| **User** | the user id | Requests per minute, tokens per hour, tokens per day |
| **Agent** | the agent's name | Tokens per day, maximum tokens per request |
| **Project** | the project id | Tokens per month |

Leave a field empty to set no limit of that kind.

Limits apply to chat in the dashboard, to the website widget, and to the
OpenAI-compatible API.

## What happens at the limit

Choose an **Action on limit** for each configuration:

| Action | Effect |
|---|---|
| **Warn** | The request goes through. The overrun is written to the server log. |
| **Throttle** | For the requests-per-minute limit, the request is delayed and then goes through. For token limits it behaves like Warn. |
| **Block** | The request is refused with a message saying which limit was reached and when to retry. |

Only **Block** actually stops spending. Start with Warn to learn what normal usage
looks like, then switch to Block with a limit comfortably above it.

## Add a limit

1. Click **Add Config**.
2. Choose the **Scope** and enter the **Scope ID**.
3. Fill in the limits and the action, then **Save**.

Saving a configuration for a scope and id that already has one replaces it.

## See current usage

Select a configuration to see **Usage vs Limits**: how many requests and tokens the
scope has used in the current minute, hour, day and month, against its limits. To
look up a scope that has no configuration, enter a user id, agent name or project id
and click **Load**.

## Being told before the limit

Notifications are not set here. To be told when a budget reaches, say, 80%, create
a budget rule on the **Alerts** page.

## A sensible setup for a public widget

- A **project** monthly token budget with **Block**, as the overall ceiling.
- An **agent** daily token limit with **Block**, so one bad day cannot use the month.
- On the widget's agent, a maximum tokens per request, so a single huge message
  cannot be expensive.

Requests per minute are counted per user. Widget visitors each have their own id, so
that limit protects against one visitor flooding the agent, not against many.

If MATE runs as several server processes, request counts are kept per process unless
your administrator configures Redis.
