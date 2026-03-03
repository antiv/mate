# Dashboard Features

The MATE Dashboard is a web-based management interface for the agent system.

## Access

- **URL**: `http://localhost:8000/dashboard`
- **Default Credentials**: 
  - Username: `admin` (configurable via `AUTH_USERNAME` env var)
  - Password: `mate` (configurable via `AUTH_PASSWORD` env var)

## Main Features

### 1. Dashboard Home (`/dashboard`)
- **Usage Statistics**: View token usage metrics for the last 7/30 days
- **Top Agents**: See most frequently used agents
- **Daily Usage Charts**: Visualize token consumption trends
- **Hourly Usage Patterns**: Analyze peak usage hours
- **Database Info**: View current database connection details

### 2. Agent Management (`/dashboard/agents`)
- **Agent Listing**: View all configured agents with search/filtering
  - Filter by root agent: Select a root agent to view its entire hierarchy (root + all sub-agents)
  - Search across name, type, model, parents, and description
- **Create New Agents**: Add new agent configurations with:
  - Basic info (name, type, model, description)
  - Parent agent assignments
  - Tool configuration (Google Search, Drive, etc.)
  - MCP servers configuration (multiple servers)
  - Planner configuration (PlanReActPlanner, BuiltInPlanner)
  - ADK configuration:
    - Generate Content Config (temperature, top_k, etc.)
    - Input/Output Schemas (JSON validation)
    - Include Contents ('default', 'none', or not set - controls conversation history)
  - Max iterations (for Loop agents)
  - Role-based access control
- **Edit Agents**: Modify existing agent configurations
- **Copy Agents**: Duplicate agents with a new name
- **View Agents**: Read-only view of agent details
- **Delete Agents**: Remove agent configurations
- **Filter by Hierarchy**: 
  - Dropdown populated with all root agents
  - Select a root agent to view only that root agent and all its descendants
  - "All Agents" option shows everything
  - Useful for focusing on specific agent trees in complex setups
- **Export/Import**: 
  - Export all agents to JSON file
  - Import agents from JSON file (with overwrite option)
- **Save as Template**: Create a reusable template from the selected project and root agent hierarchy (saved to `templates/agent_templates/`)
- **Monaco JSON Editor**: Advanced JSON editing with syntax highlighting and validation

### 3. Template Library (`/dashboard/templates`)
- **Template Gallery**: Pre-built agent configurations with search and category filters
- **One-Click Import**: Creates project, agents, and memory blocks; redirects to agents page
- **Categories**: support, research, code, content, demo
- **Built-in Templates**: Customer Support Bot, Research Assistant, Code Reviewer, Content Writer, Chess MATE
- **Save as Template**: From Agents page, select project + root agent, click "Save as Template" to create a new template from existing hierarchy
- See `documents/TEMPLATE_LIBRARY.md` for schema and community contribution

### 4. User Management (`/dashboard/users`)
- **User Listing**: View all registered users
- **Create Users**: Add new users with role assignments
- **Edit Users**: Modify user roles
- **Delete Users**: Remove users from the system
- **Role Management**: Assign/remove roles (admin, user, custom roles)

### 5. Database Migrations (`/dashboard/migrations`)
- **Migration History**: View all applied database migrations
- **Migration Details**: See version, name, timestamp, and checksum
- **Delete Migration**: Remove a migration record (admin operation)
- **Re-run Migration**: Delete and re-apply a specific migration
- **Run All Pending**: Apply all pending migrations

### 6. Usage Analytics (`/dashboard/usage`)
- **Analytics View**: 
  - Total requests and tokens (prompt + response)
  - Unique users and agents
  - Top agents by usage
  - Daily usage trends
  - Hourly usage patterns
  - Filter by time period (7/30/90 days)
- **Logs View (Raw Data)**: 
  - **Time Filters**: Last Hour, 3 Hours, 6 Hours, 12 Hours, 1 Day, 3 Days, 7 Days, 30 Days
  - **Pagination**: 50, 100, 200, or 500 records per page
  - **Navigation**: Previous/Next page buttons with page info
  - **Details**: Click any request ID to view full token breakdown
  - **Real-time**: Filters and pagination work dynamically without page reload
  - Request tracking with session IDs
  - Error status tracking

### 7. API Documentation (`/dashboard/docs`)
- **Interactive API Docs**: Links to agent API documentation
- **Server Control**: Start/Stop/Restart ADK server
- **Server Status**: Real-time ADK server health check

## Agent Types Supported

1. **LLM Agents**: Language model-based agents with tool integration
2. **Sequential Agents**: Execute sub-agents in sequence
3. **Parallel Agents**: Execute sub-agents concurrently
4. **Loop Agents**: Iterate over sub-agents with max iterations control

## Advanced Configuration

### MCP Servers Configuration
Configure multiple Model Context Protocol servers per agent:
```json
{
  "mcpServers": {
    "mate-chess-mcp": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.tavily.com/mcp/?tavilyApiKey=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"],
      "env": {}
    }
  }
}
```

### Planner Configuration
Choose and configure planners for root agents:
```json
{
  "type": "PlanReActPlanner"
}
```
or
```json
{
  "type": "BuiltInPlanner",
  "thinking_config": {
    "include_thoughts": true
  }
}
```

### Generate Content Configuration
Control LLM generation parameters:
```json
{
  "temperature": 0.7,
  "top_p": 0.9,
  "max_tokens": 1000
}
```

### Input/Output Schemas
Define JSON schemas for validation. Both input_schema and output_schema support JSON format and are automatically converted to Pydantic models.

For input_schema (JSON format - automatically converted to Pydantic model):
```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string"},
    "context": {"type": "object"}
  },
  "required": ["query"]
}
```

For output_schema (JSON format - automatically converted to Pydantic model):
```json
{
  "type": "object",
  "properties": {
    "result": {"type": "string"},
    "confidence": {"type": "number"},
    "metadata": {"type": "object"}
  },
  "required": ["result"]
}
```

Supported JSON schema types: string, integer, number, boolean, array, object

### Include Contents
Control whether agent receives prior conversation history:
- **Not set** (default): Uses ADK default behavior
- **'default'**: Agent receives the relevant conversation history
- **'none'**: Agent receives no prior contents (stateless mode - useful for stateless tasks or enforcing specific contexts)

## Security Features

- **HTTP Basic Authentication**: All dashboard and API endpoints require authentication
- **Bearer Token Support**: Generate tokens via `/auth/token` endpoint
- **Role-Based Access Control**: Agents can be restricted to specific user roles
- **Session Management**: Secure session handling with token generation/revocation

## Server Control

Dashboard includes integrated ADK server control:
- **Start Server**: Launch ADK web server
- **Stop Server**: Gracefully shutdown ADK server  
- **Restart Server**: Stop and start server
- **Status Check**: Real-time server availability monitoring

## Export/Import

### Export Format
Agents are exported in a standardized JSON format:
```json
{
  "export_info": {
    "timestamp": "2025-01-15T10:00:00",
    "version": "1.0",
    "total_agents": 5
  },
  "agents": [
    {
      "name": "agent_name",
      "type": "llm",
      "model_name": "gemini-1.5-flash",
      "description": "...",
      "instruction": "...",
      "parent_agents": ["parent1"],
      "allowed_for_roles": "[\"admin\"]",
      "tool_config": "{...}",
      "mcp_servers_config": "{...}",
      "planner_config": "{...}",
      "generate_content_config": "{...}",
      "input_schema": "{...}",
      "output_schema": "{...}",
      "include_contents": "...",
      "disabled": false
    }
  ]
}
```

### Import Options
- **Normal Import**: Skip existing agents with the same name
- **Overwrite Import**: Replace existing agents with imported configurations

## Dark Mode Support

Dashboard includes full dark mode support with automatic detection and manual toggle.

## Technology Stack

- **Backend**: FastAPI (Python)
- **Frontend**: HTML5, TailwindCSS, JavaScript
- **Database**: SQLAlchemy ORM (PostgreSQL, MySQL, SQLite)
- **Code Editor**: Monaco Editor for JSON editing
- **Charts**: Chart.js for usage analytics
- **Icons**: Font Awesome

## API Endpoints

### Dashboard API
- `GET /dashboard/api/stats?days=7` - Get usage statistics
- `GET /dashboard/api/usage/logs?hours=24&limit=100&page=1` - Get paginated token usage logs
- `GET /dashboard/api/users` - List all users
- `POST /dashboard/api/users` - Create new user
- `PUT /dashboard/api/users/{user_id}` - Update user
- `DELETE /dashboard/api/users/{user_id}` - Delete user
- `GET /dashboard/api/agents` - List all agents
- `POST /dashboard/api/agents` - Create new agent
- `PUT /dashboard/api/agents/{config_id}` - Update agent
- `DELETE /dashboard/api/agents/{config_id}` - Delete agent
- `GET /dashboard/api/agents/export` - Export all agents
- `POST /dashboard/api/agents/import?overwrite=false` - Import agents
- `GET /dashboard/api/templates?category=&search=` - List templates
- `GET /dashboard/api/templates/{id}` - Get template by id
- `POST /dashboard/api/templates/import` - One-click import (body: `{template_id, project_name?}`)
- `POST /dashboard/api/templates/create-from-agents` - Create template from existing agents (body: `{project_id, root_agent, template_id, template_name?, description?, category?}`)
- `GET /dashboard/api/migrations` - List migrations
- `DELETE /dashboard/api/migrations/{version}` - Delete migration
- `POST /dashboard/api/migrations/{version}/rerun` - Re-run migration
- `POST /dashboard/api/migrations/run` - Run all pending migrations

### Server Control API
- `GET /dashboard/api/server/status` - Check ADK server status
- `POST /dashboard/api/server/start` - Start ADK server
- `POST /dashboard/api/server/stop` - Stop ADK server
- `POST /dashboard/api/server/restart` - Restart ADK server

### Authentication API
- `POST /auth/token` - Generate Bearer token (requires Basic Auth)
- `DELETE /auth/token` - Revoke current Bearer token

## Environment Variables

```bash
# Authentication
AUTH_USERNAME=admin          # Dashboard username
AUTH_PASSWORD=mate          # Dashboard password

# ADK Server
ADK_HOST=127.0.0.1          # ADK server host
ADK_PORT=8001               # ADK server port

# Database
DB_TYPE=sqlite              # Database type (sqlite, postgresql, mysql)
DB_PATH=my_agent_data.db    # SQLite database path
DB_HOST=localhost           # PostgreSQL/MySQL host
DB_PORT=5432                # Database port
DB_NAME=mate_agent          # Database name
DB_USER=postgres            # Database username
DB_PASSWORD=password        # Database password

# API Keys
GOOGLE_API_KEY=your_key     # Google API key
OPENROUTER_API_KEY=your_key # OpenRouter API key
OPENAI_API_KEY=your_key     # OpenAI API key (optional)
```

## Getting Started

1. **Start the authenticated server**:
   ```bash
   python auth_server.py
   ```

2. **Access the dashboard**:
   - Open browser to `http://localhost:8000/dashboard`
   - Enter credentials (default: admin/mate)

3. **Configure agents**:
   - Navigate to Agent Management
   - Create or import agent configurations
   - Test agents through the API

4. **Monitor usage**:
   - View Dashboard Home for usage statistics
   - Check Usage Analytics for detailed logs
   - Track token consumption and costs

## Tips

- **Use Monaco Editor**: Toggle JSON editor for complex configurations
- **Export Regularly**: Keep backups of agent configurations
- **Monitor Usage**: Track token consumption to manage costs
- **Test Configurations**: Use the "Copy" feature to test variations
- **Role Management**: Implement least-privilege access with RBAC
- **Server Control**: Use dashboard to manage ADK server lifecycle

