# MATE (Multi-Agent Tree Engine) System Instructions

## Code Style
- Use Python 3.8+ for all new files
- Follow PEP 8 style guidelines with 4-space indentation
- Use type hints for function parameters and return types
- Prefer descriptive variable names over comments
- Use f-strings for string formatting
- Import statements should be grouped: stdlib, third-party, local imports

## Development Environment
- **ALWAYS activate the virtual environment** before running any Python commands
- Use `source .venv/bin/activate` before running Python scripts, tests, or imports
- This ensures correct package versions and dependencies are used
- All Python commands should be run within the activated virtual environment

## Architecture
- Follow the Agent-Manager-Tool Factory pattern
- Keep database models in `shared/utils/models.py`
- Keep agent business logic separate from database operations
- Use the AgentManager for all agent initialization and hierarchy management
- Tools should be created through the ToolFactory system
- All agents must include token usage tracking callbacks

## Server Architecture
- **Auth Server**: Always runs on port 8000 (`auth_server.py`)
- **ADK Server**: Always runs on port 8001 (`adk_main.py`)
- **All requests go through auth server** - no direct access to ADK server
- Auth server acts as a proxy to ADK server for all API requests
- Authentication is required for all requests (admin:mate by default)
- A2A (Agent-to-Agent) protocol requests are proxied through auth server

## Database
- Use SQLAlchemy ORM for all database operations
- Support PostgreSQL (production), MySQL, and SQLite (development)
- Database sessions should be properly closed in finally blocks
- Use the DatabaseClient for connection management
- Agent configurations are stored in `agents_config` table
- Token usage is logged in `token_usage_logs` table
- Create migrations for each DB changes. Migrations are in `shared/sql/migrations/`. Each DB type have separated folder: postgresql, sqlite, mysql
- Database updates should go through the migration system, not through a separate script.
- Do not run any DB update. Prepare migrations. Migrations will be run on server run.

## Agent Development
- Database agents are configured via `agents_config` table with types: 'root', 'llm', 'custom'
- Hardcoded agents are Python classes added during initialization
- Root agents handle orchestration and routing between sub-agents
- LLM agents handle domain-specific tasks with tool integration
- Custom agents include specialized tool capabilities (Google Drive, Search, etc.)
- All agents must implement proper RBAC through `allowed_for_roles`
- Agent implementations should be placed in `agents/` directory
- Each agent should have its own subdirectory with `__init__.py` and `agent.py`
- Agents without parent agents can be configured with planners (PlanReActPlanner or BuiltInPlanner)
- Planner configuration is stored as JSON in `planner_config` field
- Planner options (e.g. PlanReActPlanner, BuiltInPlanner) are configured via the dashboard; see `documents/` for feature docs.

## Tool Integration
- Use ToolFactory for creating agent tools
- MCP tools are configured via `mcp_servers_config` (JSON): use `mcpServers` with per-server `command`, `args`, and optional `env` (stdio-based). See `documents/MCP_SERVERS.md` for details.
- Google tools require proper service account credentials
- Custom tools are defined in `shared/utils/tools/custom_tools.py`
- Tool configuration is stored as JSON in `tool_config` field
- All tools should be created through the ToolFactory system in `shared/utils/tools/`

## Error Handling
- Always include try-except blocks for database operations
- Log errors using the configured logger
- Provide fallback agents when database initialization fails
- Return None for failed agent initialization, don't raise exceptions
- Include descriptive error messages in logs

## Environment Configuration
- Use environment variables for all API keys and sensitive data
- DB connection: `DB_TYPE` (sqlite, postgresql, mysql); for SQLite use `DB_PATH`; for PostgreSQL/MySQL use `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- Required: `DB_TYPE`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`. Authentication: `AUTH_USERNAME`, `AUTH_PASSWORD`
- Optional: `OPENAI_API_KEY` (e.g. MCP image generation), `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_SERVICE_ACCOUNT_INFO`, Supabase/artifact vars. See `.env.example` in project root and `shared/template_agent/.env.example`
- Model configuration via `MODEL_TYPE` (litellm or gemini)

## Testing
- Write unit tests for all new agent functionality
- Test both successful and failure scenarios
- Use mocking for external API calls
- Test files should be in `shared/test/` directory with `test_` prefix
- Include integration tests for agent hierarchies
- Always run tests as module

## Security
- Implement RBAC through callbacks on all agents
- Validate user roles before agent access
- Sanitize all user inputs to agents
- Use secure authentication headers for MCP integration
- Never log sensitive information (API keys, tokens)

## Performance
- Cache initialized agents in AgentManager
- Use connection pooling for database operations
- Implement token usage monitoring for cost tracking
- Optimize tool creation to avoid redundant initialization
- Use appropriate indexes on database tables
- Agents are scoped by project via the `projects` table; populate `agents_config.project_id` for every agent

## MCP Integration
- The system includes a fully integrated MCP (Model Context Protocol) server
- MCP server exposes various tool integrations
- MCP endpoints are available at `/mcp` with full protocol support
- Server-Sent Events (SSE) support for real-time communication
- CORS support for web-based MCP clients

## Dashboard & Web Interface
- The system includes a comprehensive web-based dashboard for management
- Dashboard provides user management, agent configuration, and usage analytics
- Web templates are located in `templates/` directory
- Static assets are in `static/` directory
- Dashboard API endpoints are available under `/dashboard/api/`
- Authentication is handled via HTTP Basic Auth with configurable credentials
- Agent management UI requires selecting a project before listing or editing agents

## Documentation
- Update README.md for any architectural changes
- Main README.md is in the project root; other feature and setup docs are in `documents/`
- Document new agent types and their configurations; include usage examples for new tools or agents
- Maintain database schema documentation in `shared/sql/README.md`
- Update this AGENTS.md when adding new patterns or requirements
- Do not create a separate README.md for each feature
