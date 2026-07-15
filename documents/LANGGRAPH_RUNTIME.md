# LangGraph Runtime

MATE can run its agent backend (port `ADK_PORT`, default 8001) on one of two
frameworks, selected by a single environment variable:

```bash
AGENT_FRAMEWORK=adk        # Google ADK (default) — adk_main.py
AGENT_FRAMEWORK=langgraph  # LangGraph — langgraph_main.py
```

The auth server, dashboard, widget, OpenAI-compat API and reverse proxy are
framework-agnostic: the LangGraph runtime **emulates ADK's HTTP/SSE wire
contract**, so no frontend or proxy changes are needed when switching.

## How it works

`shared/utils/server_control_service.py` picks the runtime script at startup
(`adk_main.py` or `langgraph_main.py`) based on `AGENT_FRAMEWORK`. Both accept
the same CLI arguments (`--host`, `--session-db-url`, `--a2a`) and read `PORT`
from the environment.

The LangGraph runtime lives in `shared/utils/langgraph/`:

| Module | Role |
|---|---|
| `api.py` | FastAPI app emulating ADK's endpoints (`/list-apps`, session CRUD, `/run_sse`, artifacts, reload) |
| `agent_builder.py` | Builds compiled LangGraph graphs from `agents_config` DB rows (react agent per `llm` agent; handoff-based multi-agent trees) |
| `model_factory.py` | Provider-prefix routing to `ChatLiteLLM` — same model strings as the ADK path (`create_model()` in `shared/utils/utils.py`) |
| `executor.py` | Run loop: RBAC → input guardrails → graph stream → event translation, output guardrails, persistence, token logging |
| `event_translator.py` | LangGraph stream → ADK Event JSON (partial deltas + complete events, functionCall/functionResponse, `transfer_to_agent`, `artifactDelta`, `usage_metadata`) |
| `tool_adapter.py` | Reuses the existing ADK-style tools unchanged: strips `tool_context` from the LLM-visible schema and injects a duck-typed `MateToolContext` at call time |
| `mcp_client.py` | Stdio MCP servers via `langchain-mcp-adapters`, reading the same `mcp_servers_config` JSON |
| `session_store.py` | `lg_sessions`/`lg_events` tables serving the ADK session wire shape (graph state lives in the LangGraph checkpointer, `lg_checkpoints.db`) |
| `artifact_adapter.py` | Reuses the existing local-folder/S3/Supabase artifact services (`ARTIFACT_SERVICE` env) |
| `hitl.py` | `require_confirmation` tools pause on a LangGraph interrupt and emit the same `adk_request_confirmation` functionCall the frontends already handle |
| `hooks.py` | RBAC, guardrails and user-profile injection — thin wrappers over the same ADK-free services the ADK callbacks use |

Sessions are **not shared between runtimes**: ADK keeps its own session tables,
LangGraph uses `lg_sessions`/`lg_events` + a checkpointer database. Switching
frameworks starts with fresh conversation history (agents, users, RBAC, token
logs, memory blocks and artifacts are shared).

## Multi-agent trees

Sub-agent delegation mirrors ADK semantics: every agent in the tree gets a
`transfer_to_agent(agent_name)` tool whose allowed targets are its sub-agents
plus its parents. A transfer moves the conversation to that agent's node and
**persists across turns** (checkpointed `current_agent`), and the SSE events
carry `actions.transfer_to_agent` plus the new `author`, exactly like ADK.
RBAC is re-checked when transferring to a target agent.

Unlike ADK, an agent that appears under multiple parents is a single node (no
`{name}_{parent}` instance cloning).

## Supported in v1

- DB-driven `llm` agents with sub-agent delegation
- Tools via the existing ToolFactory (custom functions, image tools, browser,
  memory blocks, user profile, shop, calendar, `create_agent` self-building, …)
- MCP stdio servers
- Sessions (DB-persisted, multi-turn memory), artifacts, streaming SSE
- RBAC (incl. audit + ACCESS_DENIED token logging), guardrails (block/redact,
  input + output, logged to `guardrail_logs`), user-profile injection
- Token usage tracking (`token_usage_logs`, same fields)
- Human-in-the-loop `require_confirmation` (approve/reject round-trip)

## Not supported in v1 (use `AGENT_FRAMEWORK=adk`)

- `graph` / `loop` workflow agents (a friendly error event is returned)
- `/run_live` (voice WebSocket), A2A, eval UI, ADK builder UI
- ADK planners (`planner_config`), context caching/compaction, resumability
- Input/output schemas, `include_contents`
- ADK-native tool objects (e.g. `google_search` builtin) — skipped with a warning
- ADK state namespaces `user:` / `app:` are stored session-scoped only

## LangGraph Studio (dev-only graph visualization)

The ADK runtime ships a dev UI at `/dev-ui`; the LangGraph equivalent is
[LangGraph Studio](https://langchain-ai.github.io/langgraph/cloud/how-tos/studio/quick_start/),
which requires the LangGraph Server protocol — **not** the MATE agent server on
port 8001 (that one emulates ADK's protocol, so Studio cannot connect to it).

A dev-only bridge is included: `langgraph.json` + `shared/utils/langgraph/studio_graph.py`
build one agent's graph (without the MATE checkpointer) for `langgraph dev`:

```bash
pip install "langgraph-cli[inmem]"           # dev dependency, already in the venv
STUDIO_AGENT=chess_mate_root langgraph dev --allow-blocking
```

- `STUDIO_AGENT` picks the agent (defaults to the first `AGENTS_LIST` entry).
- `--allow-blocking` is required — MATE's DB layer is synchronous.
- The command starts a dev server on **http://127.0.0.1:2024** and prints a
  Studio link; in Studio's "Configure Studio connection" dialog the Base URL is
  `http://127.0.0.1:2024` (port 2024, not 8001). Studio itself runs at
  smith.langchain.com and needs a (free) LangSmith login.

You get the graph rendered (root agent + sub-agent nodes), step-by-step run
inspection and time travel. Usage notes:

- Type your message into the **Messages** field; leave **Current Agent** empty —
  it is internal routing state that `transfer_to_agent` sets (a transferred
  conversation stays with that agent until it transfers back).
- **Studio runs bypass the entire MATE stack.** `langgraph dev` invokes the
  graph directly, so none of the executor hooks run: nothing is written to
  `token_usage_logs` (dashboard Token Usage), there are no MATE sessions/logs,
  and RBAC/guardrails are not applied — for MATE these runs never happened.
- **The LLM cost is still real.** Calls go straight to the provider
  (OpenRouter/Google/…) with the API keys from `.env`, so tokens are billed
  normally — MATE just doesn't count them, and MATE rate limits/budgets don't
  see them. Runs are visible in the `langgraph dev` terminal, Studio's Trace
  tab, and — with `LANGSMITH_TRACING=true` — in LangSmith, which shows token
  counts per LLM call (the closest substitute for MATE's token log here).
- Rule of thumb: use Studio for debugging graph structure and routing (rare,
  manual runs); everything else — agent testing, widget, real traffic — goes
  through MATE (ports 8000/8001) where everything is recorded.
- One agent tree per `langgraph dev` launch (`STUDIO_AGENT` picks it); tools
  that need the MATE session context (artifacts, session state, user profile)
  degrade gracefully.

## LangSmith tracing (optional)

The LangGraph runtime can stream full run traces to [LangSmith](https://smith.langchain.com)
— every `/run_sse` invocation appears as a tree: graph steps (agent/tools nodes,
transfers between agents), each LLM call with the full prompt and response, each
tool call with arguments and result, plus latency and token counts per step.

Enable it with environment variables only (no code changes — langchain reads
these at runtime):

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...      # smith.langchain.com → Settings → API Keys
LANGSMITH_PROJECT=mate          # optional: groups traces under Projects → mate
# LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com  # optional: EU data residency
```

Restart the server and open **smith.langchain.com → Projects → mate**.

Caveats:
- LangSmith is a hosted service — **prompts and user messages leave your
  infrastructure**. Use the EU endpoint for data residency, and prefer enabling
  tracing in dev/staging only. Trace upload is asynchronous and non-blocking:
  if LangSmith is unreachable, chats keep working (warnings in the log).
- This only traces the LangGraph runtime. The ADK runtime has its own
  OpenTelemetry tracing (`OTEL_TRACING_ENABLED`, see `documents/TRACING.md`).

## Dependencies

Added in `requirements.txt`: `langgraph`, `langchain-core`, `langchain-litellm`,
`langchain-mcp-adapters`, `langgraph-checkpoint-sqlite`. For Gemini, the
runtime maps `GOOGLE_API_KEY` → `GEMINI_API_KEY` automatically (litellm reads
the latter).

## Tests

```bash
python -m unittest shared.test.test_langgraph_event_translator \
                   shared.test.test_langgraph_tool_adapter \
                   shared.test.test_langgraph_agent_builder \
                   shared.test.test_langgraph_sessions -v
```
