# Exposed MCP Servers

MATE exposes:
1. Two built-in MCP (Model Context Protocol) servers (Image Generation, Google Drive)
2. Dynamic agent MCP servers (agents exposed as MCP endpoints)
3. Support for connecting to external MCP servers via agent configuration

## Built-in MCP Servers

### 1. Image Generation MCP Server (`/images/mcp`)

**Endpoints:**
- `GET /images/mcp/health` - Health check (no auth required)
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
  - Supports asset naming for version tracking
  - Custom model configuration

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
- `GET /agents/{agent_name}/mcp/health` - Health check (no auth required)
- `GET /agents/{agent_name}/mcp` - Server info endpoint
- `POST /agents/{agent_name}/mcp/initialize` - MCP protocol initialization
- `POST /agents/{agent_name}/mcp/tools/list` - List available tools
- `POST /agents/{agent_name}/mcp/tools/call` - Execute tool calls
- `GET /agents/{agent_name}/mcp/sse` - Server-Sent Events stream for real-time communication
- `OPTIONS /agents/{agent_name}/mcp` & `/agents/{agent_name}/mcp/sse` - CORS preflight handlers

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

Agents can connect to external MCP servers via the `mcp_servers_config` field in the `agents_config` database table. These are created using stdio connections and exposed as tools to agents (not as HTTP endpoints).

**Configuration Format:**
```json
{
  "mcpServers": {
    "server_name": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.example.com/mcp/?apiKey=..."],
      "env": {}
    }
  }
}
```

**Example - Tavily Search MCP:**
```json
{
  "mcpServers": {
    "tavily-search-mcp": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.tavily.com/mcp/?tavilyApiKey=..."],
      "env": {}
    }
  }
}
```

External MCP servers are created via `create_mcp_tools_from_config()` function which uses stdio connections (`StdioConnectionParams`) to communicate with remote MCP servers.

---

## Protocol Details

- **Protocol:** JSON-RPC 2.0
- **MCP Version:** `2024-11-05`
- **SSE Support:** Server-Sent Events for real-time communication
- **CORS:** Enabled for web-based MCP clients
- **Authentication:** All endpoints require authentication (Basic Auth or Bearer token), except health endpoints
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
        "password": "tribe"
      }
    }
  }
}
```

### Using Bearer Token Authentication

If you prefer using Bearer tokens:

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
