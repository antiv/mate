---
title: Trace Replay
summary: Play a traced conversation back on the Visual Builder canvas to see which agent ran when, on which model, and for how long.
audience: user
order: 155
covers:
  - static/js/trace-replay.js
  - static/js/agents-visual-builder.js
  - shared/utils/tracing/replay.py
  - shared/utils/dashboard/dashboard_server.py::*replay*
---

# Trace Replay

[Traces](sessions-and-traces.md#traces) list every step of a request as nested
bars. Trace Replay shows the same steps on your agent graph instead: the agents light
up in the order they ran, each shows what it is doing and how long it took, and an
agent whose model failed shows which fallback model answered.

## Before you start

Replay uses the traces MATE records, so the server needs `OTEL_TRACING_ENABLED=true`
and `OTEL_TRACES_DB_EXPORT` left on (it is on by default once tracing is). Only
conversations held after that can be replayed. Traces are recorded on the ADK
runtime; an installation on the LangGraph runtime has nothing to replay.

## Replay a conversation

1. Open **Studio → Visual Builder** and choose the project the agents belong to.
2. Click **Replay** in the canvas toolbar. A bar opens above the canvas.
3. Pick a session. The list shows traced sessions from the last week, with the
   agents that took part and the number of turns.
4. If the session had several turns, pick one; the last turn is selected first.
5. Press ▶. Change the speed (0.5× to 10×), or drag the slider to any moment.

From **Studio → Traces** you can also open a trace and click **Replay on canvas**,
which opens the Visual Builder with that session already chosen.

## What the canvas shows

| On the canvas | Meaning |
|---|---|
| Amber glow and **▶ model · time** | The agent is running. The label is the model it is calling or the tool it is using, and how long it has run so far. |
| Green border and **✓ time** | The agent has finished; the time is how long it ran in this turn. |
| Red border and **✗ time** | One of the agent's steps ended in an error. |
| **fallback: model** | The agent's own model failed and its [fallback model](fallback-model.md) answered. Hover it for the model that failed. |
| Faded | The agent has not started yet, or did not take part in this turn. |

Below the controls, the list of steps follows the replay: the steps running at that
moment are highlighted. Click a step to jump to it.

If an agent from the trace is not on the canvas, the bar says so. That happens when it
belongs to another project; choose that project to see it.

## Good to know

- Replay shows timing and order, not content. To read what an agent was asked and
  what it answered, open the session in [Session Tracking](sessions-and-traces.md#session-tracking).
- Fallbacks show from the time this feature was added; older traces do not record
  them.
- The replay is read-only. While it is open you can still edit the graph, but the
  coloured states describe the recorded turn, not your changes.
