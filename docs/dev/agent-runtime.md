---
title: Agent runtime
summary: How an agent row becomes a running agent, which callbacks wrap every model call, and how the ADK and LangGraph runtimes differ.
audience: dev
order: 20
covers:
  - shared/utils/agent_manager.py
  - shared/callbacks/**
  - shared/utils/fallback_agent.py
  - shared/utils/langgraph/**
  - shared/utils/provider_keys.py
---

# Agent runtime

An agent is a row in `agents_config`. This page follows that row to a running
agent, and then through one model call.

## From row to agent

`AgentManager` (`shared/utils/agent_manager.py`) does the building.

1. `initialize_agent_hierarchy(root_name)` loads the root's row and builds the tree
   under it. Children are the rows of the parent's workspace whose `parent_agents`
   JSON list contains the parent's name (`get_subagents`; a parent with no row is the
   default workspace's). LangGraph's `_load_child_configs` looks them up the same way.
2. For each row, `initialize_agent_from_config` turns the columns into a plain dict
   and `_initialize_agent` constructs the object for its `type`:

   | `type` | Built as | Notes |
   |---|---|---|
   | `llm` | ADK `Agent` | Model, instruction, tools, sub-agents, planner, callbacks. |
   | `graph` | `GraphWorkflow` | Edges, router nodes and join nodes come from `planner_config`. |
   | `loop` | `GraphWorkflow` with a loop condition | Stops at `max_iterations`. |

3. Tools come from `ToolFactory.create_tools(config)`; see
   [Writing a tool](writing-a-tool.md).
4. The model object comes from the row's `model_name`, and `model_base_url` /
   `model_api_key` when set. The prefix of the name selects the provider.
   The model is built by `create_model()` (`shared/utils/utils.py`), or
   `create_chat_model()` on LangGraph, with a `ModelContext`: the agent's name and
   project. See [Who a model is built for](#who-a-model-is-built-for).

Details that matter when something does not behave:

- Each `llm` agent gets `output_key = "<name>_output"`. That is the state key a graph
  router reads.
- `tools` inside `generate_content_config` are removed with a warning. Tools belong
  to the agent, not to the generation config.
- An agent with sub-agents, or with parents, and no description logs a warning:
  delegation picks a target by description.
- An agent that appears under several parents is built once per parent on ADK.
- Each agent built from a row records which config it was built from, a hash and the
  stored version that holds it, for its model call spans and Trace Replay
  ([Tracing](tracing.md#which-config-answered)).

### Who a model is built for

Every call to `create_model()` and `create_chat_model()` passes a `ModelContext`
(`shared/utils/utils.py`), the agent and project the model serves:

| Model | Context |
|---|---|
| An agent's own model (both runtimes) | The agent, from its row (`model_context_of(config)`) |
| Its fallback model | The same agent |
| A subagent started by `subagent_delegation` | The parent agent: the subagent works for it |
| The context-compaction summarizer | The root agent, by name (`ModelContext(agent_name=...)`): its workspace is the agent's |
| The fallback agent shown when configuration fails to load | `ModelContext()`, the installation |

The context decides two things:

- which key a model call uses (below);
- whether a model with a base URL, for an agent outside the default workspace, gets
  its completion client wrapped so that no redirect is followed into the server's
  network ([Requests to the server's own network](auth-and-access.md#requests-to-the-servers-own-network)).

`shared/test/test_model_context.py` reads the source and fails on a call to either
function that leaves `context=` out, so pass `ModelContext()` when a model serves no
agent.

### Which key a model call uses

`shared/utils/provider_keys.py::model_credentials` decides, in this order:

1. The agent's own `model_api_key` or `model_base_url`. `create_model()` and
   `create_chat_model()` receive them and leave the model as it is.
2. The workspace's key for the model's provider (`workspace_provider_keys`, V049),
   sent to the row's base URL, else to the provider's public endpoint
   (`PROVIDERS`). The public endpoint is passed explicitly, so a variable such as
   `OPENAI_BASE_URL` doesn't redirect a workspace's key.
3. The environment, as 1.x does, when the workspace may use platform keys
   (`workspaces.allow_platform_keys`). The default workspace always may, whatever
   the column says, and Platform → Workspaces refuses to turn it off for it.
4. Otherwise `NoProviderKey`: `This workspace has no key for <provider>`. A key
   that doesn't decrypt, and an `ollama` entry with no base URL, fail the same way
   instead of falling back to the environment.

`provider_of()` maps a model name to a provider: `gemini-*`, `models/*` and
`gemini/` are `google`, `ollama/` and `ollama_chat/` are `ollama`, and any other
prefix names itself. A provider outside `PROVIDERS` (`lm_studio`, `together_ai`, ...)
can't have a workspace key, so only the environment serves it.

The lookup runs on every call, not when the agent is built: `with_provider_keys()`
wraps the LiteLLM client (ADK's `llm_client`, ChatLiteLLM's `client`) in
`ProviderKeyCompletions`, which reads the workspace and its key (one indexed read
and a decryption) before each `completion` or `acompletion`. A saved, changed or
deleted key, and the workspace's flag, apply to the next call without a reload.
With the environment allowed it passes the call on unchanged; with a workspace key
it replaces `api_key` and `api_base`. `NoProviderKey` is raised before any request,
and the fallback model doesn't answer it, since it isn't a provider outage. A model
for no agent (`ModelContext()`) is left unwrapped and uses the environment.

Some providers repeat the key they were sent in their error, and the error reaches
logs (ADK logs a failed call's traceback, with the errors it was raised from) and
responses (an eval run answers with it). So a call made with a workspace key masks
it, as `••••` and its last four characters, in the error and every error in its
chain (`redact`): `ProviderKeyCompletions`, a streamed answer read later
(`_RedactingStream`), `WorkspaceGemini` and the direct calls (`redacting()`). The
error keeps its type, so the fallback model decides as before.

`shared/test/test_provider_keys_end_to_end.py` checks the phase as a whole: a
workspace set up over HTTP with only an OpenRouter key, and no use of the platform's,
runs a test case whose agent calls the image tool, judged by a model. Outgoing
requests are caught at httpx: each carries the workspace's key, none the platform's
keys in the environment, and the key shows in no response, audit entry or log line,
even when the provider repeats it.

A `gemini-*` model on ADK's native Gemini backend is a `WorkspaceGemini`, a
subclass of ADK's `Gemini`, so the built-in tools and the context cache still see a
Gemini. Each `generate_content_async` and `connect` (`/run_live`) reads the
workspace's `google` key; with one, it hands the call to a `Gemini` built with that
key (`client_kwargs`, Vertex AI off, base URL `GOOGLE_API_ROOT` so
`GOOGLE_GEMINI_BASE_URL` doesn't redirect it), kept until the key changes. Without
one it runs as ADK's `Gemini`, on `GOOGLE_API_KEY`. File Search's `genai.Client` takes
the same arguments from `google_client_kwargs()`: a query from the agent's context,
creating a store from its project, an upload from the store's project. google.genai
sends through aiohttp, outside the outbound guard, so `google_credentials()` refuses
a workspace's own Google base URL outside the default workspace; a `gemini/` model
reaches it through LiteLLM, guarded. On LangGraph, Gemini goes through LiteLLM
already.

- A Google base URL of the workspace's own is given as LiteLLM takes it, with its
  version (`.../v1beta`, the field's placeholder). `google_endpoint()` takes the
  version apart for google.genai, which adds it itself.
- ADK's live flow asks the model for `_api_backend` (session resumption); a
  `WorkspaceGemini` answers for the workspace's key, the Gemini API, not for the
  environment's client, which `GOOGLE_GENAI_USE_VERTEXAI` may make Vertex AI.
- `connect` masks the key in a live session's errors, as `generate_content_async`
  does.

Calls that go to `litellm` directly don't go through `create_model()`. They take
their context and call through `provider_keys.completion()`, or add
`litellm_credentials()`'s arguments themselves, under `guarded()` when those name
the workspace's own endpoint outside the default workspace:

| Call | Context |
|---|---|
| The eval judge | The test case's agent |
| The improver (**Suggest a fix**) | The case's or rating's agent |
| Work Room session titles | The session's agent |
| The hallucination guardrail | The agent (`GuardrailEngine(..., context)`, both runtimes; every check is given it, so a new one that calls a model has it too) |
| Memory block embeddings | The block's project |
| The image tool | Its agent; also picks the workspace's image model |
| The murder-mystery game master | Its agent |

A bare model name there is LiteLLM's to place (`litellm_provider_of`: `dall-e-3` is
`openai`), where `create_model()` sends it to Gemini. Where a call fails open today
(embeddings, the hallucination check, titles) a missing key fails it the same way.
The wizard's site analyzer stays on the environment: it is the platform's own.

### Reloading

Agents are built once and cached. Saving an agent in the dashboard posts to
`/api/reload-agent/<name>` on the agent server, which clears the caches for that
agent and its parents; the next request rebuilds it. `/api/reload-all-agents` clears
everything. Editing the database by hand needs one of these calls or a restart.

## Around every model call

For an `llm` agent, these callbacks are attached unless plugin mode is on:

| When | Callback | Does |
|---|---|---|
| before the model | `combined_user_profile_and_rbac_callback` | Injects the user's profile, checks the user's roles against the agent's, runs input guardrails. Returning a response here skips the model. |
| after the model | `guardrail_after_model_callback`, then `log_token_usage_callback` | Runs output guardrails, then writes a `token_usage_logs` row for whatever is returned. |
| on a model error | `record_model_error_callback`, then the fallback callback when configured | Writes an `ERROR` row; with `fallback_model` set, retries the request on that model. |

They live in `shared/callbacks/`. The role check is described from the user's side in
[Users and roles](../user/users-and-roles.md); it has no bypass for administrators.
It is skipped only for three internal agent names and for eval runs, which are
marked by a context variable that a request cannot set.

### Plugin mode

With `MATE_PLUGINS_ENABLED=true`, the same work is done once for the whole app by
`MatePlugin` (`shared/callbacks/mate_plugin.py`) instead of per agent, which also
covers agents created at runtime. The per-agent callbacks are then not attached, so
nothing runs twice. Requires ADK 2.x.

### Fallback model

When `fallback_model` is set and differs from `model_name`, a model error triggers a
retry on the fallback. It is reached through the provider's environment keys, never
through the agent's `model_base_url` or `model_api_key`. Each fallback is written to
the audit log as `agent.model_fallback`.

## The LangGraph runtime

`AGENT_FRAMEWORK=langgraph` replaces the agent server with
`langgraph_main.py`. Everything in front of it is unchanged, because the runtime
serves the same HTTP and SSE contract as ADK: `/list-apps`, the session routes,
`/run_sse`, artifacts and the reload routes (`shared/utils/langgraph/api.py`).

| Piece | Module in `shared/utils/langgraph/` |
|---|---|
| Graphs built from the same agent rows | `agent_builder.py` |
| The run loop: role check, guardrails, streaming, persistence, token logging | `executor.py`, `hooks.py` |
| LangGraph events translated to ADK event JSON | `event_translator.py` |
| The existing tools, reused unchanged | `tool_adapter.py` |
| Tool approval (`require_confirmation`) | `hitl.py` |
| Sessions | `session_store.py` (`lg_sessions`, `lg_events`) |

What differs from ADK:

- **Conversations are not shared.** Each runtime has its own session tables.
  Agents, users, roles, usage logs, memory blocks and artifacts are shared.
- **A shared sub-agent is one node**, not one instance per parent.
- **The role check runs per turn, not per model call.** It covers the agent
  requested and, after a transfer, the agent the turn resumes in, plus each
  transfer ([LangGraph runtime](langgraph-runtime.md#multi-agent-trees)).
- **Tools receive a stand-in for `ToolContext`.** `tool_adapter.py` hides the
  `tool_context` parameter from the model and injects `MateToolContext`. A tool that
  uses unusual attributes of ADK's context needs checking on this runtime.
- **Tracing** goes to LangSmith rather than through the OpenTelemetry pipeline.
- The first version targets `llm` agents with delegation, the tool factory, MCP
  servers (remote and stdio), sessions, artifacts and streaming. [LangGraph runtime](langgraph-runtime.md)
  keeps the current list; check it before relying on anything else.

When you change behaviour in a callback, look for the matching code in `hooks.py`
and `executor.py`. Both runtimes call the same services underneath (role check,
guardrail engine, profile, token usage), so a change in a service reaches both; a
change in an ADK callback does not.
