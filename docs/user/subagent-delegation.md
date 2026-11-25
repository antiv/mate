---
title: "Subagent delegation"
summary: Let an agent split a task and run temporary helper agents in parallel.
audience: user
order: 35
covers:
  - shared/utils/tools/subagent_delegation_tool.py
---

# Dynamic Real-time Subagents (`subagent_delegation`)

Dynamic Subagent Delegation enables an orchestrator agent (usually a root agent) to spawn, equip, and execute ephemeral subagents in parallel at runtime within a single conversational turn (*Fan-out / Fan-in* pattern).

Instead of an agent executing multiple slow research tasks sequentially or polluting its own context window with raw web pages, search dumps, and tool outputs, it delegates specific subtasks to specialized worker subagents running concurrently.

---

## How it Works

```
                        ┌──────────────┐
                        │  Root Agent  │
                        └──────┬───────┘
                               │ calls delegate_subtasks
                               ▼
        ┌──────────────────────────────────────────────┐
        │        Subagent Delegation Orchestrator      │
        └──────┬───────────────┬───────────────┬───────┘
               │               │               │
      [Parallel Task 1] [Parallel Task 2] [Parallel Task 3]
               ▼               ▼               ▼
         ┌───────────┐   ┌───────────┐   ┌───────────┐
         │Subagent 1 │   │Subagent 2 │   │Subagent 3 │
         │Role: A    │   │Role: B    │   │Role: C    │
         │Tools: [S] │   │Tools: [B] │   │Tools: [C] │
         └─────┬─────┘   └─────┬─────┘   └─────┬─────┘
               │               │               │
               └───────────────┼───────────────┘
                               ▼
               ┌───────────────────────────────┐
               │ Aggregated Results + Metrics  │
               └───────────────┬───────────────┘
                               │ returns findings
                               ▼
                        ┌──────────────┐
                        │  Root Agent  │ (synthesizes final answer)
                        └──────────────┘
```

1. **Ephemeral Execution**: Subagents run entirely in-memory (`InMemorySessionService` + ADK `Runner`). They do not create persistent rows in `agents_config` and do not clutter the database.
2. **Context Hygiene (Anti-Bloat)**: Each subagent executes in its own isolated memory and context window. Only its final distilled response is returned to the root agent.
3. **Parallel Concurrency**: All subtasks run simultaneously via `asyncio.gather`, dramatically reducing total latency.
4. **Selective Tool Assignment**: Subagents are equipped with only the specific tools requested for their task (`ToolFactory`), minimizing tool selection errors and hallucinations.
5. **Token Tracking & Audit**: Tokens consumed by subagents are recorded into `token_usage_logs` under the parent's `user_id`, with a session id that starts with the parent's `session_id`.
6. **The parent's guardrails**: a subagent has no configuration of its own, so it runs under its parent's guardrails.
7. **A short leash**: a subagent is told to make one or two tool calls and answer. From its fourth tool call on, each call returns "Tool call limit reached" instead of running.

---

## Privilege boundary

**A subagent can only be equipped with tools its parent already holds.** Anything
else named in a subtask is dropped and logged.

This is the only boundary there is. A subagent is not a row in `agents_config`,
so it has no RBAC of its own, no widget key of its own, and nothing downstream
constrains what it is handed — which means without this rule the orchestrator's
prompt would decide the child's privileges. Since that prompt can be influenced by
whoever is talking to the agent, a request to spawn a child with a shell would be
one injected instruction away.

Three consequences worth knowing:

- **The parent's widget exposure is the child's.** `code_executor` is refused for
  an agent reachable through a widget key, and a child of such an agent is
  refused it too. The check is the factory's own, called rather than restated, so
  the two cannot drift apart.
- **A child inherits the parent's settings for a tool, not a bare `true`.** A
  subagent given `shop` gets the parent's catalog and vendor details, not an
  empty default that would behave differently.
- **A child given the agent tools (`create_agent`) works for its parent.** Its
  own name has no configuration, so the tools judge it as the parent: they reach
  only agents of the parent's workspace, and outside the Default workspace they add
  nothing only a platform administrator may set. A caller the tools can't place, an
  agent with no configuration and no code in `agents/`, is refused.

MCP works the same way: a subagent's MCP servers are the parent's, so a parent
with none grants none.

## Configuring the Tool

Enable the tool on any agent in `tool_config`:

### Simple enablement (default settings)
```json
{
  "subagent_delegation": true
}
```

### Advanced configuration
```json
{
  "subagent_delegation": {
    "max_subagents": 4,
    "timeout_seconds": 60.0,
    "default_model": "gemini-2.5-flash"
  }
}
```

| Option | Type | Default | Description |
|---|---|---|---|
| `max_subagents` | integer | `5` | Maximum number of subtasks that can be executed concurrently in a single call. Extra tasks are capped. |
| `timeout_seconds` | float | `60.0` | Maximum execution time in seconds for each subagent, at most 90. If a subagent times out, other tasks continue uninterrupted. |
| `default_model` | string | `null` | Default model for subagents if not specified by the task. Without it, the parent agent's model, then `GEMINI_MODEL`, then `gemini-2.5-flash`. |

---

## Tool Signature: `delegate_subtasks`

When enabled, the agent has access to `delegate_subtasks(tasks=[...])`:

### Parameters
Each item in `tasks` accepts:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **Yes** | Identifier for the subtask (e.g. `research_competitor_a`, `data_analysis`). |
| `instruction` | string | **Yes** | Detailed instructions and goal for this subagent. |
| `role` | string | No | Persona or specialty (e.g. `Market Analyst`, `Python Engineer`). |
| `tools` | list[string] | No | Names of tools needed by the subagent (e.g. `["google_search", "browser", "code_executor"]`). |
| `model` | string | No | Model override (e.g. `gemini-2.5-flash`, `openrouter/deepseek/deepseek-chat`, `ollama_chat/llama3.2`). |

A subagent on the parent agent's own model also uses the parent's **Model Base URL**
and **Model API Key**, read the same way as for the parent: a `${VAR}` key is read
from the server environment, and the subagent fails with an error when that variable
is not set. On any other model, from the task or from `default_model`, it uses the
workspace's key for that provider, or the server's where the workspace may use it
([Models and keys](workspaces.md#models-and-keys)), and the parent's endpoint and
key are not used. A subagent of an agent in a [model group](model-groups.md) with Own
servers only fails on a model outside such a group.

### Supported Tool Names & Aliases
A subagent can be given these tools, by name or alias, and only those its parent holds:
- **Web & Search**: `google_search` (or `search`, `web_search`), `browser` (or `web_browser`, `playwright`)
- **Code & Execution**: `code_executor` (or `python`, `code`, `bash`, `terminal`)
- **RAG & Storage**: `file_search` (or `files`, `rag`), `memory_blocks` (or `memory`), `google_drive` (or `drive`), `supabase_storage`
- **Productivity**: `google_calendar` (or `calendar`), `image_tools` (or `image`, `generate_image`), `cv_tools`, `image_data_extraction`, `user_profile`, `shop`, `custom_functions`
- **MCP Protocols**: `mcp` (the parent's MCP servers)

---

## Security & Safety Guardrails

1. **Fork Bomb Guard (Recursion Depth = 1)**:
   Subagents are strictly forbidden from having `subagent_delegation` or `delegate_subtasks`. Even if requested in `tools`, delegation tools are automatically stripped from the subagent's toolset.
2. **Timeout Protection**:
   Each subagent runs under an isolated `asyncio.wait_for` timeout. If a subagent times out or errors, it returns `{ "status": "timeout" }` or `{ "status": "error" }`, allowing the remaining subtasks to complete normally.
3. **Widget Security**:
   Widget security constraints (e.g. `code_executor` blocking on public widgets without `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET`) apply equally to subagents.

---

## Example Orchestration Prompt

To encourage your root agent to use dynamic subagents effectively, include instructions like this in the agent's prompt:

```markdown
When asked to compare multiple entities, research distinct topics, or process separate datasets:
1. Break down the problem into independent subtasks.
2. Use the `delegate_subtasks` tool to spawn parallel subagents for each topic.
3. Equip each subagent with only the relevant tools (e.g. `google_search` or `browser` for research).
4. Synthesize the aggregated results into a clear, unified comparison.
```

