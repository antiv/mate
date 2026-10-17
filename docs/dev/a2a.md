---
title: "A2A (agent-to-agent)"
summary: Expose MATE agents over the A2A protocol and bridge them via MCP.
audience: dev
order: 52
covers:
  - adk_main.py
  - shared/utils/server_control_service.py
  - shared/utils/tools/agent_mcp_protocol_handler.py
---

# A2A (Agent-to-Agent) Support

This document explains how to use the A2A (Agent-to-Agent) server support in MATE.

## Overview

A2A support allows agents to be exposed using the standardized A2A protocol. When enabled, active agents discovered at ADK startup are automatically exposed as A2A endpoints, including:

- **File-based agents** (from `agents/` directory)
- **Database-configured agents** (enabled agents from `agents_config` table)
- **ADK runners** (loaded runners on the ADK web server)

Agents are exposed once during ADK startup. Any agent created or enabled later will not have an `/a2a` route until the ADK server is restarted. In addition, tasks live in an `InMemoryTaskStore`, meaning `tasks/get` only resolves tasks created within the current server process.

## Enabling A2A Support

To enable A2A support when running the ADK process standalone, start the server with the `--a2a` flag:

```bash
python adk_main.py --a2a
```

When managed via the dashboard server control (`ServerControlService`), `--a2a` is passed automatically when launching the ADK worker subprocess.

Note: The experimental LangGraph runtime (`langgraph_main.py`) accepts `--a2a` for launch-contract parity but logs a warning and ignores it.

## Agent Configuration and Cards

By default, an A2A `AgentCard` is generated automatically for each agent with streaming enabled and general skill definitions. 

Note that the default card hardcodes `"url": "http://localhost:8000"` for every agent. A client following the card will use this address unless overridden.

You can customize metadata by placing an `agent.json` file inside the agent directory (`agents/<agent_name>/agent.json`). This file is shallow-merged over the defaults (`agent_card_data.update(...)`). Because `agent.json` is only read from the filesystem under `agents/<name>/agent.json`, database-configured agents effectively always use the default card.

```json
{
  "name": "agent_name",
  "description": "Agent description",
  "version": "1.0.0",
  "url": "http://localhost:8000",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "defaultInputModes": ["application/json"],
  "defaultOutputModes": ["application/json"],
  "skills": [
    {
      "id": "general",
      "name": "general",
      "description": "General capabilities of agent_name",
      "inputMimeTypes": ["application/json"],
      "outputMimeTypes": ["application/json"],
      "tags": ["general", "a2a"]
    }
  ],
  "metadata": {
    "author": "MATE (Multi-Agent Tree Engine)",
    "created": "2025-01-27",
    "tags": ["a2a", "auto-generated"]
  }
}
```

## Endpoints and Access Control

When A2A is enabled on the ADK web server, the following endpoints are registered per agent:

- `POST /a2a/{agent_name}`: JSON-RPC 2.0 RPC endpoint for agent communication
- `GET /a2a/{agent_name}/.well-known/agent-card.json`: A2A Agent Card specification

For example:
- `http://localhost:8000/a2a/chess_mate_root`
- `http://localhost:8000/a2a/chess_mate_root/.well-known/agent-card.json`

### Access Control

When accessed through the auth proxy server (`server/proxy_routes.py`), `/a2a/*` endpoints are restricted to admin users (`_non_admin_refusal` refuses everything except `list-apps` and the caller's own run paths). Non-admin users attempting to call `/a2a/*` receive a `403 Forbidden`.

## MCP Integration

MATE includes an MCP protocol handler (`shared/utils/tools/agent_mcp_protocol_handler.py`) that bridges Model Context Protocol tool requests to agents. When an external client invokes an agent via MCP (`/agents/{agent}/mcp/tools/call`), the handler converts the call into an A2A `message/send` payload and forwards it to `http://<adk_host>:<adk_port>/a2a/{agent_name}`. Exposing an agent over MCP therefore depends on `--a2a` being enabled on the ADK server.

## Requirements

- Python 3.10 or above (required for A2A protocol packages)
- The `a2a-sdk` package (pinned to `1.2.1` in `requirements.lock`) and `google-adk` installed in the environment

## A2A Message Format

A2A uses JSON-RPC 2.0 with the `message/send` method. Here is an example request:

```bash
curl -u admin:mate -X POST http://localhost:8000/a2a/chess_mate_root \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "unique-message-id",
        "role": "user",
        "parts": [
          {
            "kind": "text",
            "text": "Your message here"
          }
        ]
      }
    },
    "id": 1
  }'
```

### Message Structure

- **`jsonrpc`**: Always `"2.0"`
- **`method`**: Use `"message/send"` for sending messages
- **`params.message.messageId`**: Unique identifier for the message
- **`params.message.role`**: Message role (`"user"`, `"assistant"`, etc.)
- **`params.message.parts`**: Array of message parts
  - **`kind`**: Type of part (`"text"`, `"file"`, `"data"`)
  - **`text`**: The actual text content
- **`id`**: Request ID for JSON-RPC

### Available Protocol Methods

- `message/send` - Send a message to the agent
- `message/stream` - Send a streaming message
- `tasks/get` - Get task information
- `tasks/cancel` - Cancel a task
- `agent/getAuthenticatedExtendedCard` - Get extended agent card with authentication

