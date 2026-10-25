---
title: Usage and audit logs
summary: See how much your agents are used and how well they answer, and the record of who changed what.
audience: user
order: 130
covers:
  - templates/dashboard/usage.html
  - templates/dashboard/index.html
  - templates/dashboard/audit_logs.html
  - shared/utils/audit_service.py
  - shared/utils/token_usage_service.py
  - shared/utils/response_metrics.py
  - shared/utils/model_pricing.py
  - shared/utils/dashboard/dashboard_server.py::*usage*
  - shared/utils/dashboard/dashboard_server.py::*audit*
  - shared/utils/dashboard/dashboard_server.py::*quality*
  - shared/utils/dashboard/dashboard_server.py::*helpful*
  - shared/utils/dashboard/dashboard_server.py::*rated*
---

# Usage and audit logs

Three pages in the Control Room answer three different questions: is the
installation healthy, how are the agents being used, and who changed what.

## Platform Overview

The landing page of the Control Room. It shows the last seven days at a glance
(requests, tokens, active users, number of agents, the busiest agents) and whether
the agent server, the database, authentication and the rate limiter are up.

## Usage Analytics

Choose a period at the top. The **Analytics** view shows:

| Figure | Meaning |
|---|---|
| **Total Requests**, **Prompt Tokens**, **Response Tokens** | Volume. Tokens are what model providers bill for. |
| **Unique Users**, **Active Agents** | How many different people and agents were involved. |
| **Satisfaction** | The share of thumbs-up among answers that were rated. Unrated answers do not count. |
| **Response Latency** | How long answers took: p50 is the typical answer, p95 the slow ones. |
| **Tokens per Conversation** | The median size of a conversation. |

Below are what the period cost (see [Cost](#cost)), a daily trend, the split between
prompt and response tokens, the busiest agents, activity by hour, satisfaction per
agent, and a per-agent table with average response time and success rate.

### Cost

Every successful model call is priced in US dollars when it is logged, at the list
price of its model (see [Model prices](settings.md#model-prices) for where prices come
from and how to set one).

| Figure | Meaning |
|---|---|
| **Cost (USD)** | What the period's calls cost. Calls without a price are not in it. |
| **Fallback Model Cost** | The part of it answered by agents' [fallback models](fallback-model.md) after their own model failed. |
| **Without a Price** | The share of tokens whose model has no known price. When it is high, the cost leaves much out; admins can follow **set prices** to fix that. |
| **Cost by Agent**, **Cost by Project** | The same cost per agent and per project, with the calls that have no price. An agent row also shows its fallback cost. |

A call counts toward the project its agent was in when the call was made, so moving,
renaming or deleting the agent later does not move its cost. Calls from before
MATE 1.6 are counted toward their agent's project at the time of the upgrade. Calls of
agents that were already deleted then, and of projects deleted since, are counted
under **No project**. Failed calls and refusals are never priced.

### Cost per helpful answer

**Satisfaction by Agent** has a column **Cost per helpful answer**: what each answer
rated helpful cost, counting the answers rated not helpful too. It is the cost of
every answer rated in the period, divided by the number rated helpful. An agent that
is cheap per call but often rated not helpful costs more per useful answer than its
call cost suggests.

- Only rated answers count, since an unrated one says nothing about success.
- An answer's cost is every call it made, including the sub-agents it handed to. The
  figure belongs to the agent the person was talking to.
- Each answer is counted on its own. When someone asks again because the first
  answer was poor, only the rated answer is counted, not the one before it.
- Calls without a price are not in the cost; their number is shown next to it.
- An agent with fewer than 10 rated answers shows the figure in grey: one rating
  more or less moves it a lot. An agent with no helpful answer shows
  **no helpful answers**.

### Which traffic you are looking at

Latency is only meaningful for comparable traffic, so it has its own selector:

| Selection | Covers |
|---|---|
| **Interactive** | People chatting in the dashboard or widget, and calls through the API. The default. |
| **Chat**, **API**, **Slack** | Each of those on its own. |
| **Triggers** | Scheduled and webhook runs. |
| **Evals** | Test runs. |
| **All** | Everything. |

### Individual requests

The **Raw Data** view lists every model call with its time, user, agent, model, token
counts and status. Open a row for the details. This is where to look when one person
reports a problem, or when a cost spike needs explaining.

People without the admin role see **My Usage** in the Work Room: the same figures for
their own requests only. Satisfaction counts the ratings given in their own
conversations, and cost covers their own calls. Their **Raw Data** view lists their own
model calls from the last 24 hours; changing the period or page size there does not
load more, since the API behind those controls is for admins.

## Audit Logs

The audit log records who did what, and when. Entries cannot be edited, and are
removed only by the retention setting described below.

What is recorded:

- agents created, changed, cloned, deleted and rolled back to an earlier version
- the AI disclosure being switched off or back on
- an agent falling back to its fallback model
- users and projects created, changed and deleted
- sign-ins and sign-outs
- **every refusal of a person by an agent's role check**
- widget keys and rate limits created, changed and deleted
- templates imported, created, synced and deleted
- database migrations, and the agent server being started, stopped or restarted

Filter by actor, action (for example `agent.update` or `rbac.denial`), resource and
date range. **Export CSV** and **Export JSON** download what the filter shows.

### How long entries are kept

By default, forever. Your administrator can set a retention period in days
(`AUDIT_RETENTION_DAYS`); the page shows the current setting, and **Run retention**
removes entries older than that now.

### The IP column

Behind a reverse proxy, the address shown is the visitor's only if the proxy is
listed as trusted on the server (`TRUSTED_PROXY_HOSTS`). Otherwise it is the proxy's
own address.
