---
title: Rate limits and budgets
summary: Cap how many requests, tokens and dollars a user, an agent or a project may use, and choose what happens at the cap.
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

Any scope can also have a budget in **US dollars per day** and **per month**.

Leave a field empty to set no limit of that kind.

### Budgets in dollars

A dollar budget is what the scope may spend over the last 24 hours (**USD/day**) or
the last 30 days (**USD/month**). Spend is the sum of the [cost](usage-and-audit.md#cost)
of each call, at the model's list price.

- Each dollar budget is measured in its own scope: a user's budget against what that
  user spent, an agent's against the agent's calls, a project's against all its agents.
- **Calls whose model has no price do not count**, unless you tick **Treat calls
  without a price as over the dollar budget**. If an agent uses a model without a
  price, set one under [Model prices](settings.md#model-prices), or its spend stays
  invisible to the budget. With the box ticked, any successful call without a price
  in the period counts as reaching the budget, so **Block** refuses further requests
  until that call is more than 24 hours (or 30 days) old, or its model gets a price.
  Use it where a model can lose its price, for example `openrouter/` models on a
  server that cannot fetch OpenRouter's price list.
- A budget of 0 refuses every priced call (with **Block**).
- If the spend cannot be read from the database, a **Block** budget refuses the
  request ("Budget check failed"); **Warn** and **Throttle** let it through and log it.

### Including sub-agents

An agent's budgets count only the calls logged under that agent's name. A sub-agent
logs under its own name, so by default a root agent's budget leaves out what its
sub-agents use. Tick **Include sub-agents** on an agent config to count the agent and
every agent below it (following the parent agents set on each agent) in its daily
token limit and its dollar budgets. The option does nothing for user and project
configs; a project budget already counts every agent in the project.

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
scope has used in the current minute, hour, day and month, and what it spent in
dollars over the last day and 30 days, against its limits. For an agent config with
**Include sub-agents**, the spend and daily tokens include the sub-agents. When the
scope had calls without a price, a warning under the spend gives their number and
says whether this budget ignores them or treats them as over budget. To
look up a scope that has no configuration, enter a user id, agent name or project id
and click **Load**.

## Being told before the limit

Notifications are not set here. To be told when a budget reaches, say, 80%, create
a budget rule on the **Alerts** page.

## A sensible setup for a public widget

- A **project** monthly budget with **Block**, as the overall ceiling. In dollars
  it is easier to set from what you are willing to pay; in tokens it also covers
  models without a price.
- An **agent** daily token limit with **Block**, so one bad day cannot use the month.
- On the widget's agent, a maximum tokens per request, so a single huge message
  cannot be expensive.

Requests per minute are counted per user. Widget visitors each have their own id, so
that limit protects against one visitor flooding the agent, not against many. A
request that is refused, for example because it names another user, is not counted.

If MATE runs as several server processes, request counts are kept per process unless
your administrator configures Redis.
