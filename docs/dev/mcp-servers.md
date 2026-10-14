---
title: "Exposed MCP servers"
summary: The MCP servers MATE serves (images, Google Drive, agents): endpoints, tools and client setup.
audience: dev
order: 54
status: migrated
covers:
  - shared/utils/mcp/**
---

# Exposed MCP Servers

MATE exposes:
1. Two built-in MCP (Model Context Protocol) servers (Image Generation, Google Drive)
2. Dynamic agent MCP servers (agents exposed as MCP endpoints)
3. Support for connecting to external MCP servers via agent configuration

## Built-in MCP Servers

### 1. Image Generation MCP Server (`/images/mcp`)

**Endpoints:**
- `GET /images/mcp/health` - Health check (no auth required; status only)
- `GET /images/mcp` - Server info endpoint
- `POST /images/mcp/initialize` - MCP protocol initialization
- `POST /images/mcp/tools/list` - List available tools
- `POST /images/mcp/tools/call` - Execute tool calls
- `GET /images/mcp/sse` - Server-Sent Events stream for real-time communication
- `OPTIONS /images/mcp` & `/images/mcp/sse` - CORS preflight handlers

**Available Tools (3):**
- `generate_image_gpt_image_1` - Generate images using GPT Image 1 model
  - Sizes: 256x256, 512x512, 1024x1024
  - Supports multiple images (1-10)
- `generate_image_dall_e_3` - Generate images using DALL-E 3 model
  - Sizes: 1024x1024, 1024x1792, 1792x1024
  - Quality: standard, hd
  - Single image only
- `generate_image_nano_banana` - Generate images using Nano Banana (Gemini 2.5 Flash Image via OpenRouter) model
  - `asset_name` is accepted and ignored; `model_config` is no longer taken from callers
  - All three go through the agents' image tool path; see [Image generation](image-generation.md)

Every route except the health check needs a signed-in caller.

**Requirements:** Image generation API keys configured (OpenAI, Google, etc.)

---

### 2. Google Drive MCP Server (`/gdrive/mcp`)

**Endpoints:**
- `GET /gdrive/mcp/health` - Health check (no auth required)
- `GET /gdrive/mcp` - Server info endpoint
- `POST /gdrive/mcp/initialize` - MCP protocol initialization
- `POST /gdrive/mcp/tools/list` - List available tools
- `POST /gdrive/mcp/tools/call` - Execute tool calls
- `GET /gdrive/mcp/sse` - Server-Sent Events stream for real-time communication
- `OPTIONS /gdrive/mcp` & `/gdrive/mcp/sse` - CORS preflight handlers

**Available Tools (7):**
- `list_files_in_folder` - List all files in a Google Drive folder
  - Uses `GOOGLE_DRIVE_FOLDER_ID` env var if folder_id not provided
- `read_google_doc` - Read content from a Google Doc/PDF/text file by file ID
- `read_google_doc_by_name` - Read content from a Google Doc by searching for its name in a folder
- `search_files` - Search for files in a Google Drive folder using a query
- `get_file_metadata` - Get metadata for a specific Google Drive file
- `get_file_sharing_permissions` - Get detailed sharing permissions for a file, including email addresses
- `find_by_name` - Find files by name (returns single match with content, or list for multiple matches)

Every route except the health check needs a caller, the same callers as the agent
MCP servers (see [Who may call](#who-may-call)): the tools read Drive with the
server's service account.

**Requirements:** `GOOGLE_SERVICE_ACCOUNT_INFO` environment variable must be set

---

### 3. Agent MCP Servers (`/agents/{agent_name}/mcp`)

**Dynamic MCP servers created for each configured agent**, allowing agents to be accessed via standard MCP protocol.

**Configuration:**
Set the `MCP_EXPOSED_AGENTS` environment variable with a comma or space-separated list of agent names:

```bash
MCP_EXPOSED_AGENTS=creative_agent,main_entity,jira_agent
```

**Endpoints (for each agent):**
- `GET /agents/{agent_name}/mcp/health` - Health check (no auth required; shows the agent's name and description)
- `GET /agents/{agent_name}/mcp` - Server info endpoint
- `POST /agents/{agent_name}/mcp/initialize` - MCP protocol initialization
- `POST /agents/{agent_name}/mcp/tools/list` - List available tools
- `POST /agents/{agent_name}/mcp/tools/call` - Execute tool calls
- `GET /agents/{agent_name}/mcp/sse` - Server-Sent Events stream for real-time communication
- `OPTIONS /agents/{agent_name}/mcp` & `/agents/{agent_name}/mcp/sse` - CORS preflight handlers

`GET /mcp` lists the exposed agents the caller may use:

```json
{"agents": [{"name": "creative_agent", "description": "...",
             "url": "http://localhost:8000/agents/creative_agent/mcp"}]}
```

#### Who may call

Every route except health and CORS preflight, and `GET /mcp`, needs a caller:

- **Credentials:** a personal access token (`Authorization: Bearer mate_pat_...`),
  Basic auth, the dashboard's login token or a dashboard session. Without one the
  route answers 401.
- **API role:** the user is an admin or holds a role in `ALLOWED_API_ROLES`
  (default `admin`, `developer`), as for the OpenAI-compatible API. Otherwise 403.
- **The agent's roles:** the user's roles must grant the agent (`allowed_for_roles`;
  an agent with none is admin-only). Otherwise 403, and `GET /mcp` leaves the agent
  out.

`shared/utils/mcp/mcp_auth.py` does the check, using `get_pat_user` from
`server/pat_auth.py` and `RBACMiddleware.check_agent_access`. It sits on the route
because the tool call reaches the agent over A2A without the caller's identity.
`/gdrive/mcp` applies the first two checks.

**Available Tools (1 per agent):**
- `call_{agent_name}_agent` - Call the agent with a message
  - **Parameters:**
    - `message` (required): The message to send to the agent
    - `session_id` (optional): Session ID for maintaining conversation context
  - **Returns:** Agent's response as text content

**How It Works:**
- Agent MCP servers are created dynamically at server startup
- Reads agent configurations from the `agents_config` database table
- Only agents listed in `MCP_EXPOSED_AGENTS` are exposed
- Each agent becomes accessible via MCP protocol
- Agent descriptions from the database are used as MCP server descriptions
- Tool calls are routed to agents via A2A (Agent-to-Agent) protocol

**Example Usage:**

```bash
# List tools for an agent
curl -u admin:mate -X POST http://localhost:8000/agents/creative_agent/mcp/tools/list \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'

# Call an agent
curl -u admin:mate -X POST http://localhost:8000/agents/creative_agent/mcp/tools/call \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "call_creative_agent_agent",
      "arguments": {
        "message": "Write a creative story about a robot"
      }
    },
    "id": 1
  }'
```

**Requirements:**
- Agents must exist in the `agents_config` table
- Agents must not be disabled (`disabled = false`)
- Agent names must be listed in `MCP_EXPOSED_AGENTS` environment variable
- ADK server must be running (agents are called via A2A protocol)

---

## External MCP Servers

Agents can connect to external MCP servers via the `mcp_servers_config` field in the `agents_config` database table. The remote server's tools are exposed to the agent as ordinary tools.

Two transports are supported, chosen by what the server entry contains:

| Entry has | Transport | Use for |
|-----------|-----------|---------|
| `url` | Streamable HTTP (or SSE) | Remote servers reachable over the network |
| `command` + `args` | stdio | Servers that run as a local subprocess |

If an entry sets both, `url` wins and the command is ignored.

### HTTP servers

**Configuration Format:**
```json
{
  "mcpServers": {
    "server_name": {
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer YOUR_TOKEN"},
      "transport": "streamable_http",
      "timeout": 60
    }
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Server endpoint. Must be `http` or `https`; anything else is refused |
| `headers` | object | `{}` | Extra request headers, typically `Authorization` |
| `transport` | string | `streamable_http` | `streamable_http` or `sse` for older servers. `type` is accepted as a synonym |
| `timeout` | number | 60 | How long a tool call may take, in seconds. Raise it for slow tools |
| `connect_timeout` | number | 5 | How long to wait for the connection itself |

This replaces the older workaround of wrapping a remote server in `npx mcp-remote`
over stdio, which needed Node on the host and a subprocess per server.

### Secrets: `${VAR}` interpolation

Any string value in a server entry may reference the server's environment as
`${VAR}` — in `headers`, `url`, `command`, `args` or `env`. The value is read from
the environment when the toolset is built, so credentials stay out of the
`agents_config` row and out of config exports.

```json
{
  "mcpServers": {
    "tavily": {
      "url": "https://mcp.tavily.com/mcp",
      "headers": {"Authorization": "Bearer ${TAVILY_API_KEY}"}
    }
  }
}
```

Rules:

- Only `${NAME}` is a placeholder. A bare `$NAME` is left alone.
- **If a referenced variable is unset, the server is skipped** and the agent
  answers without its tools, with the missing names logged. Sending the request
  without the credential, or with the literal text `${NAME}` as the credential,
  are both worse failures.
- A variable that is set but empty is treated as a real value, not a miss.

Note that interpolation reads the server's environment, so anyone who can edit an
agent's MCP config can route an environment value to the URL they choose. That is
the same trust level agent configuration already carries.

### stdio servers

**Configuration Format:**
```json
{
  "mcpServers": {
    "server_name": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.example.com/mcp/?apiKey=..."],
      "env": {},
      "timeout": 60
    }
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | string | required | Command to execute (e.g. `npx`) |
| `args` | array | required | Command arguments |
| `env` | object | `{}` | Environment variables |
| `timeout` | number | 60 | Request timeout in seconds. Use higher values (e.g. 300) for slow tools like `tavily_research`. |

**Example - Tavily Search MCP:**
```json
{
  "mcpServers": {
    "tavily-search-mcp": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.tavily.com/mcp/?tavilyApiKey=..."],
      "env": {},
      "timeout": 300
    }
  }
}
```

External MCP servers are created via `create_mcp_tools_from_config()`, which dispatches on the server entry: `create_mcp_toolset_http()` for a `url` (`StreamableHTTPConnectionParams` or `SseConnectionParams`) and `create_mcp_toolset_command()` for `command`/`args` (`StdioConnectionParams`).

---

## Protocol Details

- **Protocol:** JSON-RPC 2.0
- **MCP Version:** `2024-11-05`
- **SSE Support:** Server-Sent Events for real-time communication
- **CORS:** Enabled for web-based MCP clients
- **Authentication:** Every endpoint except health and CORS preflight requires a caller. Agent servers, `GET /mcp` and `/gdrive/mcp` take a personal access token, Basic auth, the dashboard's login token or a session ([Who may call](#who-may-call)); `/images/mcp` takes a signed-in dashboard user and no personal access tokens
- **Availability:** Both servers check availability at startup and return 503 if dependencies aren't configured

---

## Implementation

- **Built-in Server Classes:** `ImageMCPServer` and `GoogleDriveMCPServer` in `shared/utils/mcp/`
- **Built-in Protocol Handlers:** `ImageMCPProtocolHandler` and `GoogleDriveMCPProtocolHandler` in `shared/utils/tools/`
- **Agent MCP Manager:** `AgentMCPManager` in `shared/utils/mcp/agent_mcp_manager.py` - manages dynamic agent MCP server creation
- **Agent MCP Server:** `AgentMCPServer` in `shared/utils/mcp/agent_mcp_server.py` - creates MCP endpoints for individual agents
- **Agent MCP Protocol Handler:** `AgentMCPProtocolHandler` in `shared/utils/tools/agent_mcp_protocol_handler.py` - handles agent tool calls via A2A protocol
- **Initialization:** MCP servers are initialized in `auth_server.py` via `initialize_mcp_servers()`
- **Tool Creation:** External MCP tools are created via `create_mcp_tools_from_config()` in `shared/utils/tools/mcp_tools.py`

## Environment Variables

```bash
# Configure which agents to expose as MCP servers (comma or space-separated)
MCP_EXPOSED_AGENTS=creative_agent,main_entity,jira_agent

# ADK server configuration (for agent MCP calls)
ADK_HOST=127.0.0.1
ADK_PORT=8001
```

---

## MCP Client Configuration Examples

### Claude Desktop

Add to your Claude Desktop configuration file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "mate-chess-mate-root": {
      "url": "http://localhost:8000/agents/creative_agent/mcp",
      "headers": {
        "Authorization": "Basic YWRtaW46bWF0ZQ=="
      }
    },
    "mate-main-entity": {
      "url": "http://localhost:8000/agents/main_entity/mcp",
      "headers": {
        "Authorization": "Basic YWRtaW46bWF0ZQ=="
      }
    },
    "mate-image-generation": {
      "url": "http://localhost:8000/images/mcp",
      "headers": {
        "Authorization": "Basic YWRtaW46bWF0ZQ=="
      }
    },
    "mate-google-drive": {
      "url": "http://localhost:8000/gdrive/mcp",
      "headers": {
        "Authorization": "Basic YWRtaW46bWF0ZQ=="
      }
    }
  }
}
```

**Note:** `YWRtaW46bWF0ZQ==` is the base64 encoding of `admin:mate` (default credentials). Replace with your actual credentials if different.

### Cursor IDE

Add to your Cursor MCP configuration (`~/.cursor/mcp.json` or similar):

```json
{
  "mcpServers": {
    "mate-chess-mate-root": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8000/agents/creative_agent/mcp",
        "--header",
        "Authorization: Basic YWRtaW46bWF0ZQ=="
      ]
    },
    "mate-main-entity": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8000/agents/main_entity/mcp",
        "--header",
        "Authorization: Basic YWRtaW46bWF0ZQ=="
      ]
    }
  }
}
```

### Generic HTTP MCP Client

For clients that support HTTP-based MCP servers:

```json
{
  "mcpServers": {
    "mate-chess-mate-root": {
      "type": "http",
      "url": "http://localhost:8000/agents/creative_agent/mcp",
      "auth": {
        "type": "basic",
        "username": "admin",
        "password": "mate"
      }
    }
  }
}
```

### Using Bearer Token Authentication

For the agent servers and `/gdrive/mcp`, use a personal access token from the
account menu in the dashboard: `"Authorization": "Bearer mate_pat_..."`. It lasts
until it expires or is revoked.

A login token works for every server, including `/images/mcp`, but expires after
`TOKEN_TTL_HOURS` (default 24):

1. **Get a token:**
   ```bash
   curl -u admin:mate -X POST http://localhost:8000/auth/token
   ```

2. **Use in MCP client config:**
   ```json
   {
     "mcpServers": {
       "mate-chess-mate-root": {
         "url": "http://localhost:8000/agents/creative_agent/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_TOKEN_HERE"
         }
       }
     }
   }
   ```

### Testing MCP Connection

Test your MCP server connection:

```bash
# Health check
curl http://localhost:8000/agents/creative_agent/mcp/health

# Initialize
curl -u admin:mate -X POST http://localhost:8000/agents/creative_agent/mcp/initialize \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}'

# List tools
curl -u admin:mate -X POST http://localhost:8000/agents/creative_agent/mcp/tools/list \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}'

# Call agent
curl -u admin:mate -X POST http://localhost:8000/agents/creative_agent/mcp/tools/call \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "call_creative_agent_agent",
      "arguments": {
        "message": "Hello, agent!"
      }
    },
    "id": 1
  }'
```
