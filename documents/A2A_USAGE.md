# A2A (Agent-to-Agent) Support

This document explains how to use the A2A (Agent-to-Agent) client support in MATE.

## Overview

A2A support allows agents to communicate with each other using a standardized protocol. When enabled, **all agents** in the system will be automatically exposed as A2A endpoints, including:

- **File-based agents** (from `agents/` directory)
- **Database-configured agents** (created dynamically from `agents_config` table)

## Enabling A2A Support

To enable A2A support, start the server with the `--a2a` flag:

```bash
python adk_main.py --a2a
```

This will automatically enable A2A for **all agents** in the system - both file-based and database-configured.

## Agent Configuration

A2A support works with all existing agents automatically. However, you can optionally create custom `agent.json` files to provide specific A2A metadata. Here's the structure:

```json
{
  "name": "agent_name",
  "description": "Agent description",
  "version": "1.0.0",
  "capabilities": [
    "text_generation",
    "conversation"
  ],
  "endpoints": {
    "rpc": "/a2a/agent_name",
    "agent_card": "/a2a/agent_name/.well-known/agent-card"
  },
  "metadata": {
    "author": "Your Name",
    "created": "2025-01-27",
    "tags": ["tag1", "tag2"]
  }
}
```

## Example Usage

1. **Start the server with A2A support:**
   ```bash
   python adk_main.py --a2a
   ```

2. **Access any agent via A2A endpoints:**
   - RPC endpoint: `http://localhost:8000/a2a/{agent_name}`
   - Agent card: `http://localhost:8000/a2a/{agent_name}/.well-known/agent-card.json`

   For example:
   - **File-based agents**: `http://localhost:8000/a2a/chess_mate_root`
   - **Database-configured agents**: `http://localhost:8000/a2a/chess_mate_root`, `http://localhost:8000/a2a/chess_opening_book`

## Automatic Agent Discovery

The system automatically discovers **all agents** in the system and enables A2A for them:

1. **File-based agents** - from the `agents/` directory
2. **Database-configured agents** - from the `agents_config` table (created on-the-fly)
3. **ADK runners** - comprehensive list from the ADK web server

No special configuration is required - A2A support works with all existing agents automatically.

## Requirements

- Python 3.10 or above (for A2A dependencies)
- A2A dependencies must be installed (handled automatically by ADK)

## Troubleshooting

- If A2A dependencies are not available, the system will log a warning and continue without A2A support
- Check the server logs for A2A setup messages
- Ensure your agent has a valid `agent.json` file
- Verify the agent directory structure follows the expected format

## Endpoints

When A2A is enabled, the following endpoints are automatically created for each configured agent:

- `POST /a2a/{agent_name}` - RPC endpoint for agent communication (JSON-RPC with `message/send` method)
- `GET /a2a/{agent_name}/.well-known/agent-card.json` - Agent card information

## A2A Message Format

A2A uses JSON-RPC 2.0 with the `message/send` method. Here's the correct format:

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

### Available Methods

- `message/send` - Send a message to the agent
- `message/stream` - Send a streaming message
- `tasks/get` - Get task information
- `tasks/cancel` - Cancel a task
- `agent/getAuthenticatedExtendedCard` - Get extended agent card with authentication
