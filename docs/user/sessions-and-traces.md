---
title: Sessions and traces
summary: Look inside a conversation to see what an agent was asked, which tools it called and what the model returned.
audience: user
order: 150
covers:
  - templates/dashboard/sessions.html
  - static/js/sessions.js
  - templates/dashboard/traces.html
  - static/js/traces.js
  - shared/utils/tracing/**
  - shared/utils/dashboard/dashboard_server.py::*session*
  - shared/utils/dashboard/dashboard_server.py::*trace*
---

# Sessions and traces

When an agent gives a strange answer, these two pages show why. **Session Tracking**
shows the conversation as the agent experienced it. **Traces** shows the timing of
every step.

## Session Tracking

**Control Room → Session Tracking** lists conversations from every source: the Work
Room, widgets, Slack, the API and triggers.

Filter by agent, by user, or search for words that appeared in the conversation or
for a session id. Open a session to see the **Session Inspector**: each turn in
order, with the prompt the model received, its reasoning where the model exposes
it, the tools it called with their arguments and results, and the final answer.

What to look for:

- **The wrong sub-agent answered.** The inspector shows which agent took each turn.
  Routing follows descriptions, so sharpen the description of the agent that should
  have been chosen.
- **A tool returned an error or nothing.** The agent usually carries on and makes the
  best of it. The tool result in the inspector shows what it actually got back.
- **The agent ignored an instruction.** Check the prompt it received: if the
  instruction is loaded from a memory block, see whether the block was read at all.

This page shows what people wrote to your agents. It is for administrators only,
and what you read there deserves the same care as any other customer data.

## Traces

A trace is the timeline of one request: every model call, tool call and hand-off
between agents, with how long each took. Use it when the question is "why was this
slow" rather than "why was this wrong".

**Studio → Traces** lists recent traces with their total duration and number of
steps. Open one to see the steps nested inside each other; the long bar is your
answer.

> Traces are recorded only when your administrator has set
> `OTEL_TRACING_ENABLED=true` on the server. Without it the page stays empty.

Traces use OpenTelemetry, so the same data can also be sent to an external tool your
organisation already uses for monitoring. They are recorded on the ADK runtime; an
installation running the LangGraph runtime traces to LangSmith instead.

## Which page for which question

| Question | Page |
|---|---|
| What did the agent see and do in this conversation? | Session Tracking |
| Why did this request take so long? | Traces |
| How many requests and tokens, and how satisfied are people? | [Usage Analytics](usage-and-audit.md) |
| Was a message blocked or altered? | [Guardrail Logs](guardrails.md#see-what-fired) |
| Who changed this agent? | [Audit Logs](usage-and-audit.md#audit-logs) |
