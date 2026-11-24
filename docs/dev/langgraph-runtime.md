---
title: "LangGraph runtime"
summary: Run the agent server on LangGraph instead of ADK: how it works, what is supported, what differs.
audience: dev
order: 22
covers:
  - langgraph_main.py
  - shared/utils/langgraph/**
  - shared/utils/server_control_service.py
---

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
| `model_factory.py` | Provider-prefix routing to `ChatLiteLLM` — same model strings as the ADK path (`create_model()` in `shared/utils/utils.py`), and the same `ModelContext` ([Agent runtime](agent-runtime.md#who-a-model-is-built-for)); outside the default workspace, a `model_base_url` gets the same redirect guard (`outbound.guard_model_client` on the model's `client`); without an endpoint of the agent's own, the `client` takes the workspace's key on each call (`provider_keys.with_provider_keys`, [Agent runtime](agent-runtime.md#which-key-a-model-call-uses)), `gemini/` models included |
| `executor.py` | Run loop: RBAC → input guardrails → graph stream → event translation, output guardrails, persistence, token logging, and approval requests, each answer passing `approval_service.gate_answer` before the graph resumes on it (see [Approval requests](approvals.md)) |
| `event_translator.py` | LangGraph stream → ADK Event JSON (partial deltas + complete events, functionCall/functionResponse, `transfer_to_agent`, `artifactDelta`, `usage_metadata`) |
| `tool_adapter.py` | Reuses the existing ADK-style tools unchanged: strips `tool_context` from the LLM-visible schema and injects a duck-typed `MateToolContext` at call time |
| `mcp_client.py` | MCP servers via the MCP SDK's client (mcp 2), reading the same `mcp_servers_config` JSON: `url` entries over streamable HTTP or SSE, `command` entries over stdio, with the same `${VAR}`, scheme and timeout rules as ADK, and the same refusals: stdio without the `code` platform feature on the agent's workspace (`platform_only.may_use(FEATURE_CODE, ...)`, by the agent's project), and outside the default workspace a `url` on the server's own network (`outbound.outbound_refusal`). A server that cannot be reached costs only its own tools. Each call opens a session of its own; a result marked as an error is a failed tool call, with the server's text; tool names are the server's, without a prefix. A streamable HTTP event may have 16 MB (`MAX_EVENT_BYTES`), an SSE one the SDK's 1 MB. |
| `session_store.py` | `lg_sessions`/`lg_events` tables serving the ADK session wire shape (graph state lives in the LangGraph checkpointer, `lg_checkpoints.db`). Deleting a session deletes its checkpoint thread first, and creating one with a given id deletes any checkpoint left under that id, so a new session never resumes an old conversation. `PATCH` on a session merges `stateDelta` into its state, as ADK's does. Deleting an agent deletes its sessions and their checkpoints (`shared/utils/agent_cleanup.py`). |
| `artifact_adapter.py` | Reuses the existing local-folder/S3/Supabase artifact services (`ARTIFACT_SERVICE` env) |
| `hitl.py` | `require_confirmation` tools pause on a LangGraph interrupt and emit the same `adk_request_confirmation` functionCall the frontends already handle. The pause carries the model's tool call id (injected as `tool_call_id`, hidden from the model), and the confirmation call's id is `adk-confirm-<hash>` of that id, the tool and its arguments: the same each time the tool re-runs, and another for another call when the model repeats an id. The pause keeps it as `confirmationId`, which the event leaves out. Calls the model makes in one step pause together: the executor resumes the interrupts whose confirmation ids the answer names, handing each the whole decision map, and a call resumed without its own decision pauses again. The model is called once every call has been answered, as on ADK with resumability on. While a sibling is still paused, a finished call keeps listing its interrupt in the state's tasks, so a call that already has a ToolMessage counts as answered (`finished_tool_call_ids`); the confirmations and results the caller already has are not sent again. |
| `hooks.py` | RBAC, guardrails and user-profile injection — thin wrappers over the same ADK-free services the ADK callbacks use |

Sessions are **not shared between runtimes**: ADK keeps its own session tables,
LangGraph uses `lg_sessions`/`lg_events` + a checkpointer database. Switching
frameworks starts with fresh conversation history (agents, users, RBAC, token
logs, memory blocks and artifacts are shared).

## Schemas and include_contents

These agent settings behave as they do on ADK, with the same JSON-schema-to-Pydantic
conversion (`json_schema_to_pydantic_model`). A schema that is not valid JSON or not an
`object` schema is logged and ignored, on both runtimes.

- **`output_schema`**: the agent keeps its tools and transfers, and ends its turn by
  calling `set_model_response`, a tool whose arguments are the schema (ADK's name for
  the same mechanism). The call returns directly, so the run ends there. The event
  stream carries the answer as one JSON text event, as ADK does. The text is the
  tool's validated output (after Pydantic coercion and defaults), not the raw
  arguments, and the call itself is not shown. The prompt tells the model to answer
  through that tool. If the arguments do not match the schema, the call and the
  tool's error are sent as a function call and response and the run ends with no
  JSON text. A model that answers in plain text without calling
  the tool is not forced to; its text is returned as it is.
- **`include_contents`**: `none` shows the model the messages from the latest user
  message on (so the current turn's tool calls and results stay), as on ADK. The
  checkpoint still holds the whole conversation. Empty or `default` shows all of it.
- **`input_schema`**: parsed and checked like `output_schema`, with no effect at run
  time. On ADK it only shapes an agent called through `AgentTool`, which MATE does
  not use, so neither runtime validates an agent's input.

## Multi-agent trees

Sub-agent delegation mirrors ADK semantics: every agent in the tree gets a
`transfer_to_agent(agent_name)` tool whose allowed targets are its sub-agents
plus its parents. A transfer moves the conversation to that agent's node and
**persists across turns** (checkpointed `current_agent`), and the SSE events
carry `actions.transfer_to_agent` plus the new `author`, exactly like ADK.
The user's roles are checked for the target when transferring, and again at the
start of every later turn for the agent the turn resumes in: `execute_run` checks
the agent requested, then reads the checkpointed `current_agent` and checks that
agent too when it is another one. So a user who loses a sub-agent's role, or whose
sub-agent changes its allowed roles, is refused there on the next turn instead of
going on with it, as ADK refuses on the sub-agent's next model call. The session
stays with that agent, so the user starts a new conversation. A checkpoint that
cannot be read refuses the turn.

A transfer with no run context, so no user to check, is refused. Only the graph
built for LangGraph Studio (`studio_graph.py`, `AgentBuilder(require_run_context=False)`)
transfers without one.

Unlike ADK, an agent that appears under multiple parents is a single node (no
`{name}_{parent}` instance cloning).

## Supported in v1

- DB-driven `llm` agents with sub-agent delegation
- Tools via the existing ToolFactory (custom functions, image tools, browser,
  memory blocks, user profile, shop, calendar, `create_agent` self-building, …)
- File Search: the stores assigned to an agent in the dashboard are added to its
  `tool_config` before ToolFactory runs, as on ADK (`add_assigned_stores`,
  `shared/test/test_file_search_assigned_stores.py`)
- MCP servers, remote (`url`, streamable HTTP or SSE) and stdio. `timeout` applies
  to remote servers; stdio servers on this runtime use the adapter's default.
- Sessions (DB-persisted, multi-turn memory), artifacts, streaming SSE
- RBAC (incl. audit + ACCESS_DENIED token logging), guardrails (block/redact,
  input + output, logged to `guardrail_logs`), user-profile injection
- Token usage tracking (`token_usage_logs`, same fields)
- Human-in-the-loop `require_confirmation` (approve/reject round-trip)
- Evals against a stored version and "Check against suite" (see [Evals](evals.md))
- `output_schema`, `input_schema` and `include_contents`, as on ADK (below)

## Not supported in v1 (use `AGENT_FRAMEWORK=adk`)

- `graph` / `loop` workflow agents (a friendly error event is returned)
- `/run_live` (voice WebSocket), A2A, eval UI, ADK builder UI
- ADK planners (`planner_config`), context caching/compaction, resumability
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

Never run `langgraph dev` on a production server or expose its port: it has no
MATE login, RBAC or guardrails (see below), and it is not installed in the
Docker image. The same goes for ADK's dev UI, which is off by default when
`MATE_ENV=production`.

- `STUDIO_AGENT` picks the agent (defaults to the first `AGENTS_LIST` entry, then
  to the first enabled top-level agent in the database).
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
  and RBAC/guardrails are not applied, transfers included — for MATE these runs
  never happened.
- **The LLM cost is still real.** Calls go straight to the provider
  (OpenRouter/Google/…) with the keys the agent would use in MATE (its workspace's,
  or `.env`'s where the workspace may use them), so tokens are billed normally — MATE just doesn't count them, and MATE rate limits/budgets don't
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
  OpenTelemetry tracing (`OTEL_TRACING_ENABLED`, see `docs/dev/tracing.md`).

## Dependencies

Added in `requirements.txt`: `langgraph`, `langchain-core`, `langchain-litellm`,
`langgraph-checkpoint-sqlite`. For Gemini, the
runtime maps `GOOGLE_API_KEY` → `GEMINI_API_KEY` automatically (litellm reads
the latter).

## Tests

```bash
python -m unittest discover -s shared/test -t . -p "test_langgraph_*.py" -v
```

That covers the event translator, tool adapter, agent builder, schemas and
`include_contents`, client tools, remote MCP, resumed-agent RBAC and sessions. `-t .` matters: without it the tests skip the
package setup and use the database `.env` configures. Tests of other features cover
the runtime too, such as `test_model_fallback.py`, `test_eval_version_runner.py`,
`test_rbac_fail_closed.py` and `test_file_search_assigned_stores.py`.
