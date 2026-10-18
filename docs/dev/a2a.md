---
title: "A2A (agent-to-agent)"
summary: Expose MATE agents over the A2A protocol, and how the MCP bridge uses it.
audience: dev
order: 52
covers:
  - adk_main.py
  - shared/utils/a2a_routes.py
  - shared/utils/server_control_service.py
  - shared/utils/tools/agent_mcp_protocol_handler.py
---

# A2A (Agent-to-Agent) Support

This document explains how MATE exposes its agents over the A2A (Agent-to-Agent)
protocol. MATE serves A2A; it does not call remote A2A agents.

## Overview

With A2A enabled, every agent the ADK server finds at startup gets an A2A endpoint:

- **File-based agents** (directories under `agents/`)
- **Database-configured agents** (enabled rows in `agents_config`)
- **ADK runners** (runners already loaded on the ADK web server)

The routes are built in `shared/utils/a2a_routes.py` through `google.adk.a2a._compat`,
which works with both a2a-sdk 0.3.x and 1.x. An agent whose card or routes fail to
build is skipped and logged, and the others still mount. A missing dependency
(`ImportError`) disables A2A for all agents, with a message at startup.

Agents are exposed once, at ADK startup. An agent created or enabled later has no
`/a2a` route until the ADK server restarts. Tasks live in one `InMemoryTaskStore`
for the process, so `tasks/get` only finds tasks created since the last start.

## Enabling A2A Support

To enable A2A support when running the ADK process standalone, start the server with
the `--a2a` flag:

```bash
python adk_main.py --a2a
```

When the ADK server is started by the auth server (`ServerControlService`), `--a2a`
is always passed.

The LangGraph runtime (`langgraph_main.py`) accepts `--a2a` for launch-contract
parity but logs a warning and ignores it.

## Agent Cards

Each agent gets a default card, written in the 0.3 JSON form
(`default_card_data()` in `shared/utils/a2a_routes.py`):

```json
{
  "name": "agent_name",
  "description": "A2A-enabled agent_name agent",
  "version": "1.0.0",
  "url": "http://localhost:8000/a2a/agent_name",
  "protocolVersion": "0.3.0",
  "preferredTransport": "JSONRPC",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "general",
      "name": "general",
      "description": "General capabilities of agent_name",
      "tags": ["general", "a2a"]
    }
  ]
}
```

The card's `url` is `http://localhost:8000/a2a/{agent}` (`DEFAULT_BASE_URL`), the auth
server on its default port. In any other deployment a client that follows the card
uses the wrong address unless `agent.json` sets `url`.

To change the card, put an `agent.json` in the agent's directory
(`agents/<agent_name>/agent.json`), in the same 0.3 JSON form. `card_data_for()`
merges it over the default with a shallow `dict.update`, so a key you set (for example
`capabilities`) replaces the whole default value. An `agent.json` that can't be read
is logged and the default card is used. Only `agents/<name>/agent.json` is read, so
database-configured agents always get the default card.

On a2a-sdk 1.x, `_compat.parse_agent_card` converts the 0.3 form to a 1.x card: the
`url` becomes an entry in `supportedInterfaces`. The served card carries both forms,
so 0.3 and 1.x clients can read it.

## Endpoints and Access Control

Each agent gets two routes on the ADK server:

- `POST /a2a/{agent_name}`: the JSON-RPC 2.0 endpoint
- `GET /a2a/{agent_name}/.well-known/agent-card.json`: the agent's card

Through the auth server they are reached at the same paths, for example:

- `http://localhost:8000/a2a/chess_mate_root`
- `http://localhost:8000/a2a/chess_mate_root/.well-known/agent-card.json`

The auth server forwards `/a2a/*` to the ADK server for admins only.
`_non_admin_refusal` in `server/proxy_routes.py` lets a non-admin through only to
`list-apps`, their own user paths and their own `/run` and `/run_sse`; everything
else, `/a2a/*` included, gets `403`.

## MCP Integration

An agent exposed as an MCP server (`MCP_EXPOSED_AGENTS`, see
[OpenAI API and MCP](openai-api-and-mcp.md)) is answered over A2A.
`shared/utils/tools/agent_mcp_protocol_handler.py` turns an MCP `tools/call` into an
A2A `message/send` request and posts it straight to the ADK server at
`http://<adk_host>:<adk_port>/a2a/{agent_name}`, not through the auth server's proxy;
the MCP routes check the caller themselves. It reads the reply's text from the
returned message, or from the task's artifacts. Exposing an agent over MCP therefore
needs A2A enabled on the ADK server.

## Requirements

- Python 3.10 or above
- `a2a-sdk` (pinned to `1.2.1` in `requirements.lock`) and `google-adk`

## Calling an Agent

The endpoint accepts the a2a-sdk 1.x method names and, through the 0.3 compatibility
layer, the 0.3 names. `shared/test/test_a2a_routes.py` sends one request of each.

### 0.3 method names

This is the form the MCP bridge sends. No version header is needed:

```bash
curl -u admin:mate -X POST http://localhost:8000/a2a/chess_mate_root \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "kind": "message",
        "messageId": "unique-message-id",
        "role": "user",
        "parts": [{"kind": "text", "text": "Your message here"}]
      }
    }
  }'
```

- **`params.message.messageId`**: unique id of the message
- **`params.message.role`**: `"user"` or `"agent"`; any other value is refused as an
  invalid request
- **`params.message.parts`**: the message parts, each with a `kind` (`"text"`,
  `"file"` or `"data"`)

The 0.3 methods are `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`,
`tasks/resubscribe`, `tasks/pushNotificationConfig/{get,set,list,delete}` and
`agent/getAuthenticatedExtendedCard`.

### 1.x method names

1.x requests must send the `A2A-Version: 1.0` header; without it the handler treats
the request as 0.3 and refuses the 1.x method with error `-32009`. Roles are
`ROLE_USER` and `ROLE_AGENT`, and a text part is just `{"text": ...}`:

```bash
curl -u admin:mate -X POST http://localhost:8000/a2a/chess_mate_root \
  -H "Content-Type: application/json" \
  -H "A2A-Version: 1.0" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "unique-message-id",
        "role": "ROLE_USER",
        "parts": [{"text": "Your message here"}]
      }
    }
  }'
```

The 1.x methods are `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`,
`CancelTask`, `SubscribeToTask`, `CreateTaskPushNotificationConfig`,
`GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs`,
`DeleteTaskPushNotificationConfig` and `GetExtendedAgentCard`.

MATE's cards declare no extended card, so `agent/getAuthenticatedExtendedCard` and
`GetExtendedAgentCard` answer with an error.
