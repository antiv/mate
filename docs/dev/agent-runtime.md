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
---

# Agent runtime

An agent is a row in `agents_config`. This page follows that row to a running
agent, and then through one model call.

## From row to agent

`AgentManager` (`shared/utils/agent_manager.py`) does the building.

1. `initialize_agent_hierarchy(root_name)` loads the root's row and builds the tree
   under it. Children are the rows whose `parent_agents` JSON list contains the
   parent's name.
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

Details that matter when something does not behave:

- Each `llm` agent gets `output_key = "<name>_output"`. That is the state key a graph
  router reads.
- `tools` inside `generate_content_config` are removed with a warning. Tools belong
  to the agent, not to the generation config.
- An agent with sub-agents, or with parents, and no description logs a warning:
  delegation picks a target by description.
- An agent that appears under several parents is built once per parent on ADK.

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
- **Tools receive a stand-in for `ToolContext`.** `tool_adapter.py` hides the
  `tool_context` parameter from the model and injects `MateToolContext`. A tool that
  uses unusual attributes of ADK's context needs checking on this runtime.
- **Tracing** goes to LangSmith rather than through the OpenTelemetry pipeline.
- The first version targets `llm` agents with delegation, the tool factory, stdio
  MCP servers, sessions, artifacts and streaming. [LangGraph runtime](langgraph-runtime.md)
  keeps the current list; check it before relying on anything else.

When you change behaviour in a callback, look for the matching code in `hooks.py`
and `executor.py`. Both runtimes call the same services underneath (role check,
guardrail engine, profile, token usage), so a change in a service reaches both; a
change in an ADK callback does not.
