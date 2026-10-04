---
title: OpenAI-compatible API and MCP exposure
summary: Call MATE agents from tools that speak the OpenAI chat-completions protocol or MCP.
audience: dev
order: 50
covers:
  - server/openai_routes.py
  - server/openai_translate.py
  - server/pat_auth.py
  - shared/utils/mcp/**
---

# OpenAI-compatible API and MCP exposure

Two protocols let other software use MATE agents without knowing anything about
MATE: the OpenAI chat-completions API, which coding assistants and most LLM
libraries speak, and MCP.

## The OpenAI-compatible API

An exposed agent appears to the client as a model.

### Set up

1. Mark a **root** agent with **Expose as Model** in the dashboard. Sub-agents cannot
   be exposed.
2. Create a personal access token from the account menu. The user needs a role
   listed in `ALLOWED_API_ROLES` (default `admin`, `developer`).
3. Point the client at `https://<your-mate>/v1` with the token as the API key.

```bash
curl https://your-mate.example.com/v1/chat/completions \
  -H "Authorization: Bearer mate_pat_..." \
  -H "Content-Type: application/json" \
  -d '{"model": "support_root", "messages": [{"role": "user", "content": "Hello"}]}'
```

| Route | Returns |
|---|---|
| `GET /v1/models` | Active root agents with `expose_as_model` set. |
| `POST /v1/chat/completions` | One agent turn, streamed or not. |

### What the agent does with a request

The request runs the agent the same way a chat does: its own instruction, tools,
memory, guardrails and sub-agents. The model named in the request selects the agent,
not an LLM.

- **Sessions.** Each conversation maps to a persistent agent session, keyed on the
  agent and the first user message, so separate conversations stay separate even
  when they share a system prompt.
- **The client's system prompt** is passed along as context with the opening
  message. It does not replace the agent's configured instruction.
- **The user** is the owner of the token. Role checks, usage logs and rate limits
  apply to that user. One chat-completions request counts as one request against the
  per-minute limit, even when it fans out into several model calls.

### Tool calling

A coding assistant brings its own tools (read a file, run a command) that only work
on the machine it runs on. MATE accepts them and hands the calls back:

1. The client sends `tools` with the request.
2. MATE offers them to the agent's model next to the agent's own tools.
3. When the model calls a client tool, MATE does not run it. The turn pauses and
   the response carries `tool_calls` with `finish_reason: "tool_calls"`.
4. The client runs the tool and sends the result as a `role: "tool"` message.
5. MATE resumes the paused turn with that result.

Rules that follow from this:

- The agent's own tools run on the server and are never shown to the client.
- Only the exposed root agent receives client tools. Sub-agents keep exactly what
  their configuration gives them.
- If a client tool has the same name as one of the agent's, the agent's wins and the
  client's is not offered.
- `tool_choice: "none"` withdraws the client's tools for that request; naming a
  function narrows to that one.
- A request without `tools` is plain chat with the agent.

## Agents as MCP servers

Set `MCP_EXPOSED_AGENTS` to a list of agent names and each becomes an MCP server at
`/agents/<name>/mcp`, with the standard endpoints under it (`initialize`,
`tools/list`, `tools/call`, `sse`, and an unauthenticated `health`). An MCP client
that connects sees the agent as a tool it can call.

```bash
curl -u admin:... -X POST \
  https://your-mate.example.com/agents/support_root/mcp/tools/list
```

Only agents named in the setting are exposed. `AgentMCPManager`
(`shared/utils/mcp/agent_mcp_manager.py`) registers the routes at startup, so a
change to the setting needs a restart.

Two more MCP servers are built in, serving MATE's own image generation and Google
Drive tools to any MCP client: `/images/mcp` and `/gdrive/mcp`. Endpoints, tools and
client configuration are in [Exposed MCP servers](mcp-servers.md).

## Agent-to-agent (A2A)

The agent server is started with A2A client support enabled.
[A2A (agent-to-agent)](a2a.md) describes calling and exposing agents over that
protocol.

## Choosing between them

| You want | Use |
|---|---|
| An agent inside a coding assistant or an LLM library | The OpenAI-compatible API |
| An agent as a tool for another agent system | MCP exposure |
| Your own chat front end | `/run_sse` through the proxy, with a bearer token |
| A chat on a website | The [widget](../user/widget.md) |
