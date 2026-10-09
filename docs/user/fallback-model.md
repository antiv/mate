---
title: "Fallback model"
summary: Answer from a second model when an agent's own model fails.
audience: user
order: 28
status: migrated
covers:
  - shared/callbacks/model_fallback_callback.py
---

# Fallback model

When an agent's model fails (the provider is down, rate-limits the key, or
times out), the run fails and the person chatting, often a visitor on a
customer's site through the widget, sees an error. An agent with a **fallback
model** re-runs that request once on the second model and answers with it
instead.

## Configuring it

Set **Fallback Model** in the agent modal or in the visual builder's side
panel, or send `fallback_model` to `POST /dashboard/api/agents`. It takes the
same model strings as Model Name, for example `openai/gpt-4o-mini` or
`gemini-2.5-flash`. Pick a model on a different provider from the agent's own,
because an outage usually takes down a whole provider.

Leave it empty and nothing changes. A fallback equal to the agent's own model
is ignored.

The fallback is reached through the provider's server env vars
(`OPENAI_API_KEY`, `GOOGLE_API_KEY`, ...). It never uses the agent's **Model
Base URL** or **Model API Key**. Those belong to the agent's own model, and
sending that key to another provider's host would leak it.

## When it fires

The fallback runs once per failed model call, and only when the provider is
unavailable:

- a timeout or a connection failure
- a 5xx response (500, 502, 503)
- a rate limit (429)

Errors caused by the request itself do not fall back: a content-policy
refusal, a context-length error, a bad request, or an invalid key. Falling
back on those would let anyone chatting move a request past the primary
provider's own filters by provoking a refusal. They fail as they would without
a fallback. If the fallback fails too, the original error goes through as if no
fallback were set.

- **ADK runtime:** the fallback runs from the agent's `on_model_error_callback`,
  after the error is recorded as an `ERROR` row in usage logs. Retries inside
  the provider client happen first. A `retry_config` in `planner_config` re-runs
  the whole agent node, so it only applies when the fallback fails too. The
  fallback's answer goes through the same after-model callbacks as any other,
  so guardrails and token logging apply to it. It is not streamed: it arrives
  as one complete message.
- **LangGraph runtime:** the agent's model is wrapped with LangChain's
  `with_fallbacks`, so the fallback also streams.

## Seeing it

- **Usage logs:** the `model_name` of each `token_usage_logs` row is the model
  that answered, so the fallback's calls show up under its name on the
  dashboard. On ADK, the primary's failure also leaves an `ERROR` row.
- **Audit log:** each fallback writes an `agent.model_fallback` entry with the
  primary and fallback model, and on ADK the primary's error.
- **Server log:** a warning names the agent, both models and the error.

A fallback can send a request that was meant for the agent's own endpoint (for
example a private, self-hosted model) to a third-party provider. Leave the
field empty on agents whose data must not leave that endpoint.

To be told when it fires, add an [alert rule](alerts.md) on **Fallback model
used**. If the fallback fires often, fix or replace the primary. The fallback is meant
to cover outages, not to be the normal path.
