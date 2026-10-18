**Title:** Trace Replay: watch a multi-agent conversation play back on the agent graph — who ran when, every hand-off, every fallback

---

Debugging a multi-agent tree from a list of nested spans gets old fast. You know the answer was slow or wrong, but finding *which* agent took over, *when* the root handed off to a sub-agent, and *where* the time went means expanding bars and reading attribute JSON.

MATE now plays the conversation back on the same canvas you built the agents on.

[video]

## What you see

- **Agents light up in the order they ran.** A running agent glows and shows what it is doing right now: the model it is calling or the tool it is using, and how long it has run so far. When it finishes it shows its total time.
- **Hand-offs are visible.** While an agent runs a sub-agent, the edge between them lights up. When later turns go straight to that sub-agent (as ADK does after a transfer), only the sub-agent lights up.
- **Fallbacks are visible.** If an agent's primary model failed and its fallback model answered, the node says so (`fallback: gemini/gemini-2.5-flash`), with the model that failed on hover.
- **The whole session is one run.** All turns play back to back; the minutes a person spent typing between turns are cut to a short pause, and a "Turn 2 / 8" label tells you where you are. Speed from 0.5× to 10×, a slider to scrub, and a step list you can click to jump.

The session picker only offers sessions in which an agent of the project on the canvas took part, so you can't pick something the canvas can't draw.

## How it works

No new instrumentation framework: it reads the OpenTelemetry spans MATE already stores when `OTEL_TRACING_ENABLED=true`. Agents come from ADK's `invoke_agent` spans, model calls from MATE's own `gen_ai.inference` span (which now records `mate.model.fallback` when the fallback answered), tools from `execute_tool`. ADK and MATE both trace model and tool calls, so each is counted once. Each turn is one trace; the session's turns are put on one clock and the canvas is decorated from that timeline. Tested on SQLite and PostgreSQL.

You can also open it from any trace on the Traces page with **Replay on canvas**.

## Limitations

- Needs tracing on, and only covers conversations held after you turn it on.
- ADK runtime only. The LangGraph runtime traces to LangSmith, so there is nothing to replay there yet.
- It shows timing and order, not content. For prompts and answers, the Session Inspector is still the place.
- Fallbacks only show on traces recorded after this release.

Repo, docs and the user guide (`docs/user/trace-replay.md`) in the comments. I'd love to hear what you'd want on the canvas next. Token cost per node? Guardrail hits?
