**Title:** MATE now runs on LangGraph too — one env var switches the whole agent runtime (Google ADK ↔ LangGraph), same agents, same UI, zero frontend changes

---

MATE (Multi-Agent Tree Engine) has been a web platform on top of **Google ADK**: DB-driven agents, multi-LLM via LiteLLM, RBAC, guardrails, token budgets, an embeddable widget and a dashboard. As of this release the ADK dependency is no longer a hard one — set

```bash
AGENT_FRAMEWORK=langgraph   # or: adk (default)
```

and the same agents, the same dashboard, the same widget run on a **LangGraph** backend instead.

## The trick: emulate the wire protocol, not the framework

I didn't abstract the frameworks behind a common interface — that path touches every file and breaks both sides. Instead, the LangGraph runtime speaks **ADK's HTTP/SSE contract**: the session endpoints, `/run_sse` with ADK's Event JSON (`content.parts`, `functionCall`/`functionResponse`, `artifactDelta`, streaming partials + cumulative finals), even the 404-means-recreate-session semantics. The proxy, dashboard, widget and OpenAI-compat API literally cannot tell which framework is running. ~60% of the codebase (auth, DB, RBAC, guardrail engine, budgets) was already framework-free; the new runtime is one directory.

## What survived the port

- **Multi-agent trees.** ADK's `sub_agents` delegation maps to the LangGraph handoff pattern: every agent becomes a node in a parent `StateGraph`, each gets a `transfer_to_agent` tool whose allowed targets are its children + parents, and a checkpointed `current_agent` key makes transfers stick across turns — same semantics as ADK. One lesson: routing reliability lives in the *system prompt*, not the graph. My first one-line "you can transfer…" note made weak models chat about routing instead of doing it; porting ADK's exact instruction text ("…call `transfer_to_agent`. When transferring, do not generate any text other than the function call.") fixed it.
- **All existing tools, unmodified.** 17 tool modules take ADK's `tool_context` parameter. The adapter strips that parameter from the LLM-visible schema and injects a duck-typed stand-in at call time via a ContextVar — `.state`, `save_artifact`, user/session ids all work, zero edits to the tool files.
- **MCP servers** (same DB config, via langchain-mcp-adapters), **sessions**, **artifacts**, **RBAC + audit**, **guardrails** (block/redact on input and output), **token tracking**, and **human-in-the-loop** tool confirmation — reimplemented on LangGraph interrupts, same approve/reject card in the UI.

## What you get on the LangGraph side

- **LangSmith tracing**: 4 env vars and every run shows up as a full tree (graph steps, prompts, tool calls, tokens).
- **LangGraph Studio**: a dev-only bridge (`langgraph dev --allow-blocking`) renders your agent tree and lets you step through runs and time-travel. Heads-up: Studio calls the graph directly, so MATE's token logging/RBAC/guardrails don't apply there — it's a debug tool, not a serving path.

## Honest limitations (v1)

`graph`/`loop` workflow agents, voice (`/run_live`), A2A and the eval UI still need `AGENT_FRAMEWORK=adk` — they return a friendly "switch runtime" message on LangGraph. Sessions don't migrate between runtimes (different persistence models); everything else — users, agents, budgets, logs — is shared.

Repo + docs in the comments. If you've been wanting the MATE dashboard/widget/guardrails layer but your team is on LangChain infra, this one's for you. Questions welcome.
