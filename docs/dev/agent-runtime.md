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

Not yet covered: a `gemini-*` model on ADK's native Gemini backend builds its client
from `GOOGLE_API_KEY` (on LangGraph it goes through LiteLLM and is covered), and the
calls that go to `litellm` directly (next paragraph).

Calls that go to `litellm` directly do not go through `create_model()` and take
no context yet: the eval judge, the agent improver, the hallucination guardrail,
image generation, embeddings, the wizard, Work Room session titles and the
murder-mystery demo.

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
