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
5. **Token Tracking & Audit**: Tokens consumed by subagents are recorded into `token_usage_logs` linked to the parent `session_id` and `user_id`.

---

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
| `timeout_seconds` | float | `60.0` | Maximum execution time in seconds for each subagent. If a subagent times out, other tasks continue uninterrupted. |
| `default_model` | string | `null` | Default model for subagents if not specified by the task. Falls back to parent agent model or `GEMINI_MODEL`. |

---

## Tool Signature: `delegate_subtasks`

When enabled, the agent has access to `delegate_subtasks(tasks=[...])`:

### Parameters
Each item in `tasks` accepts:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **Yes** | Alphanumeric identifier for the subtask (e.g. `research_competitor_a`, `data_analysis`). |
| `instruction` | string | **Yes** | Detailed instructions and goal for this subagent. |
| `role` | string | No | Persona or specialty (e.g. `Market Analyst`, `Python Engineer`). |
| `tools` | list[string] | No | Names of tools needed by the subagent (e.g. `["google_search", "browser", "code_executor"]`). |
| `model` | string | No | Model override (e.g. `gemini-2.5-flash`, `openrouter/deepseek/deepseek-chat`, `ollama_chat/llama3.2`). |

### Supported Tool Names & Aliases
The subagent can be equipped with any tool registered in `ToolFactory`:
- **Web & Search**: `google_search` (or `web_search`), `browser` (Playwright headless)
- **Code & Execution**: `code_executor` (Python & Bash subprocesses)
- **RAG & Storage**: `file_search` (Gemini File Search store), `memory_blocks` (Persistent semantic memory), `google_drive`
- **Productivity**: `google_calendar`, `image_tools`
- **MCP Protocols**: `mcp` (inherits parent MCP server configurations)

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

