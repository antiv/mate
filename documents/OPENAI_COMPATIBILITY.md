# OpenAI Compatibility & Personal Access Tokens (PATs)

MATE provides an OpenAI-compatible API bridge, allowing external coding agents, IDE extensions, and tools to interact with MATE agents as if they were standard LLM models. 

This lets you use the full agentic reasoning, memory blocks, and local tools of your MATE agents inside interfaces like **OpenCode**, **Continue**, and **Cline (Roo Code)** — including tools the client runs on your machine.

---

## How It Works

1. **Model Discovery (`GET /v1/models`)**: External clients query this endpoint to populate their model dropdown list. MATE returns the list of active root agents that have the `expose_as_model` attribute enabled.
2. **Chat Completions (`POST /v1/chat/completions`)**: Requests are routed to the specified agent. MATE creates or resumes a persistent chat session for the conversation, executes the agent loop on the backend, and streams/returns the response in the standard OpenAI JSON/SSE structure.
3. **Tool calling**: if the request carries a `tools` array, those tools are offered to the agent's model *alongside* the agent's own MATE tools. See [Tool calling](#tool-calling) below.

---

## Tool calling

A coding agent's usefulness is its tools — reading and writing files, running commands — and those only work on the machine the client is running on. MATE therefore accepts the client's tool declarations and hands the model a merged tool set.

The round trip:

1. The client sends `tools` (and optionally `tool_choice`) with the request.
2. MATE declares them to the agent's model in addition to the agent's configured tools.
3. If the model calls one, MATE **does not execute it**. The agent turn pauses and the response comes back with `tool_calls` and `finish_reason: "tool_calls"`.
4. The client runs the tool and sends the result back as a `role: "tool"` message.
5. MATE resumes the paused turn with that result and the agent continues.

Details worth knowing:

* **Both tool sets are live.** The model may call the client's `read` and MATE's `memory_blocks` in the same conversation. MATE's own tool calls execute server-side and are never shown to the client — it cannot run them, and offering them would deadlock the conversation.
* **Only the exposed root agent gets client tools.** Sub-agents keep exactly the tools their configuration gives them; a sub-agent must never hold more than its parent.
* **Name collisions go to the agent.** If the client declares a tool whose name the agent already has, the agent's own tool wins and the client's is not declared. Rename the tool on the client side if you need both.
* **`tool_choice`**: `"none"` withdraws the client's tools for that request, and `{"type": "function", "function": {"name": "..."}}` narrows to that one. `"auto"` and `"required"` pass everything through — MATE cannot force the model to call a tool.
* **Parallel calls** are supported: the model may request several tools in one turn, and the client returns all the results in one follow-up request.
* **Requests without `tools` behave exactly as before** — plain chat against the agent, with the agent using only its own tools.

### Conversation identity

MATE maps each conversation onto a persistent agent session. The session id is derived from the agent, the system prompt and the first user message, so two conversations that share a system prompt — which every coding agent has — stay separate. Each request sends only what the runtime has not seen yet; the client's history is not replayed.

The client's system prompt is passed as context with the opening message. It does not replace the agent's configured instruction, which remains the operator's.

---

## Authentication & Authorization

All requests to the `/v1` endpoints are authenticated and authorized using:
1. **Personal Access Tokens (PATs)**: Individual users generate PATs in the MATE dashboard. The PAT is sent as a bearer token (`Authorization: Bearer mate_pat_...`). MATE stores only the SHA-256 hash of the token for security.
2. **Role Restrictions**: Access to the OpenAI compatible endpoints is restricted by user roles. By default, only users with the `admin` or `developer` roles are authorized to verify a PAT and call the API.
   * *Note: Permitted roles can be custom configured in the MATE `.env` file via `ALLOWED_API_ROLES=admin,developer`.*

---

## Step-by-Step Configuration Guide

### 1. Enable Model Exposure
An agent must be a **root agent** (it cannot have parent agents) to be exposed.
* **Via Dashboard UI**: Toggle the `Expose as Model` switch in the agent's configuration panel.
* **Via API**: Send a `PUT` request to `/dashboard/api/agents/{config_id}/expose` with a JSON payload of `{"expose": true}`.

### 2. Generate a Personal Access Token
* **Via Dashboard UI**: 
  1. Click on the user profile dropdown icon in the top right corner of the dashboard navbar.
  2. Select **Personal Tokens** from the menu.
  3. Enter a descriptive token name (e.g. "VS Code Continue") and select an expiration period, then click **Generate Token**.
  4. Copy the generated token immediately. **For security, it is only shown once.**
* **Via API**: Send a `POST` request to `/dashboard/api/tokens` with a JSON payload:
  ```json
  {
    "name": "My VS Code Token",
    "expires_in_days": 30
  }
  ```
  Save the returned raw token (`mate_pat_...`). **It will only be shown once.**

### 3. Configure External Clients

#### OpenCode (`opencode.json`)
OpenCode is an open-source terminal-native coding agent. Register MATE as a custom provider. Use the `@ai-sdk/openai-compatible` package — the plain `openai` package targets `/v1/responses`, which MATE does not serve:

```json
{
  "provider": {
    "mate": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "MATE",
      "options": {
        "baseURL": "http://localhost:8000/v1",
        "apiKey": "mate_pat_your_generated_token"
      },
      "models": {
        "your-exposed-agent-name": {
          "name": "MATE Coder Agent",
          "tool_call": true
        }
      }
    }
  }
}
```

`tool_call: true` is what lets OpenCode hand the agent its own file and shell tools. MATE reports no context window of its own (see the note under `GET /v1/models`), so set OpenCode's per-model `limit` yourself if the defaults do not match the model your agent runs on.

#### Continue (config.json)
Continue is a popular VS Code and JetBrains extension. Add MATE under the `models` list:

```json
{
  "models": [
    {
      "title": "MATE Coder Agent",
      "provider": "openai",
      "model": "your-exposed-agent-name",
      "apiBase": "http://localhost:8000/v1",
      "apiKey": "mate_pat_your_generated_token"
    }
  ]
}
```

#### Cline / Roo Code
In your VS Code Cline/Roo Code settings panel:
1. Select **OpenAI Compatible** under the Model Provider.
2. Set **Base URL** to `http://localhost:8000/v1`.
3. Set **API Key** to `mate_pat_your_generated_token`.
4. Set **Model ID** to your MATE agent's name (e.g., `chess_mate_root`).

---

## Example: Coding Agent Template (`coding-agent`)

MATE includes a pre-configured multi-agent template specifically optimized for software development. This template uses the state-of-the-art code generation model `openrouter/qwen/qwen3-coder-next` (Qwen 2.5 Coder) and connects three specialized agents:

1. **`coding_root`**: The Lead Coder (exposed as an OpenAI compatible model). It receives instructions, coordinates subagents, and generates core structures.
2. **`coding_tester`**: The Test Engineer subagent. It writes unit/integration tests and executes them inside isolated sandboxes using MATE's `code_executor` tool.
3. **`coding_security`**: The Security Auditor subagent. It scans code for OWASP Top 10 vulnerabilities, insecure dependency patterns, and secret leaks.

### Using the Coding Agent in External Tools

When you import the `Coding Agent` template in the MATE dashboard, the root agent `coding_root` is automatically created with `expose_as_model` enabled.

To use it in your external tools (like **OpenCode** or **Continue**), reference the name generated by MATE during project creation (e.g., `your_project_coding_root`):

#### OpenCode Example Config
```json
{
  "provider": {
    "mate": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "MATE",
      "options": {
        "baseURL": "http://localhost:8000/v1",
        "apiKey": "mate_pat_your_generated_token"
      },
      "models": {
        "your_project_coding_root": {
          "name": "MATE Coding Team (Qwen Coder)",
          "tool_call": true
        }
      }
    }
  }
}
```

#### Continue Example Config
```json
{
  "models": [
    {
      "title": "MATE Coding Team (Qwen Coder)",
      "provider": "openai",
      "model": "your_project_coding_root",
      "apiBase": "http://localhost:8000/v1",
      "apiKey": "mate_pat_your_generated_token"
    }
  ]
}
```

---

## Developer API endpoints reference

### Personal Access Tokens
* **`GET /dashboard/api/tokens`**: Lists all active PATs for the currently logged-in user.
* **`POST /dashboard/api/tokens`**: Generates a new PAT.
* **`DELETE /dashboard/api/tokens/{token_id}`**: Revokes and deletes a PAT.

### OpenAI Compatibility
* **`GET /v1/models`**: Lists exposed MATE models. `agents_config` carries no timestamps, so `created` is a fixed placeholder rather than a real creation date, and no context-window metadata is reported — configure limits on the client side.
* **`POST /v1/chat/completions`**: Executes chat completions (supports `stream: true` and `stream: false`, `tools`, `tool_choice`, and `role: "tool"` messages). `usage` counts only the turn being answered.
