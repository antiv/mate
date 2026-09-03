# MATE (Multi-Agent Tree Engine) System Instructions

## Project Overview
MATE (Multi-Agent Tree Engine) is a production-ready web platform built on top of Google ADK (with optional LangGraph runtime support) that adds database-driven agent management, multi-LLM support, external OpenAI-compatible agent endpoints, RBAC, MCP integration, token tracking, guardrails, EU AI Act compliance features, and a dashboard UI with Work Room code canvas.

## Code Style
- Use Python 3.8+ for all new files
- Follow PEP 8 style guidelines with 4-space indentation
- Use type hints for function parameters and return types
- Prefer descriptive variable names over comments
- Use f-strings for string formatting
- Import statements should be grouped: stdlib, third-party, local imports
- Match existing style when editing existing code

## Development Environment
- **ALWAYS activate the virtual environment** before running any Python commands
- Use `source .venv/bin/activate` before running Python scripts, tests, or imports
- This ensures correct package versions and dependencies are used
- All Python commands should be run within the activated virtual environment

### Quick Start
```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Run (auto-applies DB migrations, default SQLite)
python auth_server.py
# Dashboard: http://localhost:8000 (default login: admin/mate)

# Docker alternative
docker-compose up
```

## Architecture
- **Agent-Manager-Tool Factory pattern**: Centralized initialization and management
- **Dual-Server Design**:
  - **Auth Server**: Runs on port 8000 (`auth_server.py`, FastAPI with HTTP Basic Auth, proxies authenticated requests to the agent server)
  - **Agent Server**: Runs on port 8001 (internal, not directly exposed). Runtime framework selectable via `AGENT_FRAMEWORK`:
    - `adk` (default): Google ADK web server (`adk_main.py`)
    - `langgraph`: LangGraph runtime (`langgraph_main.py` + `shared/utils/langgraph/`, emulates ADK wire contract)
- **All requests go through auth server** — no direct external access to agent runtime server
- A2A (Agent-to-Agent) and MCP protocol requests are proxied through auth server
- Business logic is strictly separated from database operations
- `shared/callbacks/` hooks into agent lifecycle for RBAC (`rbac_callback.py`), token tracking (`token_usage_callback.py`), and guardrails (`model_guardrail.py`, `function_call_guardrail.py`)
- Rate limiting and request throttling via `server/rate_limit_middleware.py`

## Database & Migrations
- Use SQLAlchemy ORM for all database operations (models defined in `shared/utils/models.py`)
- Support PostgreSQL (production), MySQL, and SQLite (development) via `DB_TYPE`
- Database sessions must be properly closed in `finally` blocks
- Use `DatabaseClient` for connection management and pooling
- **Key Tables**:
  - `agents_config`: Agent configuration and metadata
  - `projects`: Project boundaries and isolation
  - `users`: User authentication and role management
  - `token_usage_logs`: LLM token consumption and cost tracking
  - `guardrail_logs`: Model and function call guardrail incidents
  - `audit_logs`: Append-only audit events (evidence toward EU AI Act Art. 12)
  - `rate_limit_config`: Per-user/agent/project rate limit and budget rules
  - `memory_blocks`: Persistent contextual memory blocks
  - `widget_api_keys`: Public embeddable chat widget keys and allowed origins
  - `agent_config_versions`: Historical versioning of agent configurations
- **Migrations**:
  - Migrations are stored in `shared/sql/migrations/{postgresql,mysql,sqlite}/`
  - Create migrations for every database schema change across all 3 dialects
  - Database updates should go through the migration system, not through ad-hoc scripts
  - Migrations auto-apply on server startup
  - Migration CLI commands:
    ```bash
    python shared/migrate.py run       # Apply pending migrations
    python shared/migrate.py status    # Check migration status
    python shared/migrate.py create    # Create new migration files
    python shared/migrate.py rollback  # Roll back last migration
    ```

## Agent Development
- **Database agents**: Stored in `agents_config` with types: `'root'`, `'llm'`, `'custom'`
- **Hardcoded agents**: Python classes in `agents/<agent_name>/` (`__init__.py` and `agent.py`)
- **External agents**: Agents can point to an existing OpenAI-compatible endpoint via `model_base_url` and `model_api_key` on the agent row. RBAC, guardrails, audit, token tracking, and widget integration apply identically. See `documents/EXTERNAL_AGENTS.md`
- **Agent Tree**: `shared/utils/agent_manager.py` merges database and hardcoded agents, building the hierarchy at startup
- **Project Scoping**: Agents are scoped by project via `projects` table; `agents_config.project_id` must be populated for every agent
- **Planners**: Root agents or agents without parents can be configured with planners (`PlanReActPlanner`, `BuiltInPlanner`) via `planner_config` JSON field
- **RBAC**: All agents must implement role validation through `allowed_for_roles`
- **Agent Tree Cloning**: Copy root agent hierarchies between projects, maintaining unique naming

## Tool Integration
- All tools must be constructed through `ToolFactory` (`shared/utils/tools/tool_factory.py`)
- Tool configuration is stored as JSON in `tool_config` field
- **MCP Tools**:
  - Configured via `mcp_servers_config` JSON using `mcpServers`
  - **Stdio transport**: specify `command`, `args`, and optional `env`
  - **HTTP / SSE transport**: specify `url` for direct streaming connection without subprocess overhead
  - **`${VAR}` secret interpolation**: references to `${ENV_VAR}` in MCP configs or endpoint keys resolve dynamically from the server environment, keeping credentials out of the database
  - Renamed `MCPToolset` to `McpToolset` for ADK 2.3+ compatibility
  - See `documents/MCP_SERVERS.md` for details
- **Specialized Tools**:
  - `create_agent_tool`: Allows agents to inspect, create, or modify other agents at runtime
  - Memory blocks with semantic search embeddings (`EMBEDDING_MODEL`)
  - Image generation with automated digital source marking
  - Browser automation tools (async Playwright with session management)
  - Google Drive & Google Search tools (require service account credentials)
  - Custom tools defined in `shared/utils/tools/custom_tools.py`
  - Human-in-the-loop (HITL) tool confirmation support

## EU AI Act & Compliance
- **Article 50 (AI Disclosure)**: Public chat surfaces (embed widget, standalone chat) inform users they are interacting with an AI (`shared/utils/ai_disclosure.py`). Wording is configurable per agent. Waivers require an explicit justification string and write an entry to `audit_logs`. See `documents/AI_ACT.md`
- **Article 50(2) (Marking of Generated Synthetic Content)**: Generated images carry an XMP packet declaring IPTC digital source type `trainedAlgorithmicMedia` prior to saving (`shared/utils/content_marking.py`)
- **Article 12 (Audit Logging)**: Append-only audit trail in `audit_logs` table managed by `shared/utils/audit_service.py` provides record-keeping evidence. Retention configurable via `AUDIT_RETENTION_DAYS`

## Security & Guardrails
- **RBAC**: Mandatory user role validation before agent access
- **Input Sanitization**: Sanitize user inputs and tool parameters
- **Guardrails**: Implement pre-call and post-call model guardrails (`model_guardrail.py`) and tool call constraints (`function_call_guardrail.py`)
- **Endpoint Security**:
  - Stored endpoint keys never reach the browser (sentinel values used in API responses)
  - Unresolvable `${VAR}` secrets abort agent construction rather than falling back to provider defaults
  - Agent provider keys are isolated from third-party hosts
- **Widget Security**:
  - Origin allowlist enforcement via `WIDGET_ORIGIN_STRICT`
  - `code_executor` tool is blocked on widget-exposed agents by default (`MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET` override required)
- **Production Guard**: `MATE_ENV=production` refuses insecure default credentials and misconfigurations unless explicitly overridden via `MATE_ALLOW_INSECURE_DEFAULTS`
- **Never log sensitive data** (API keys, credentials, tokens)

## Environment Configuration

Use environment variables for all API keys, database settings, and system toggles.

| Variable | Purpose | Default / Options |
|---|---|---|
| `AGENT_FRAMEWORK` | Agent runtime engine | `adk` (default) or `langgraph` |
| `DB_TYPE` | Database dialect | `sqlite` (default), `postgresql`, `mysql` |
| `DB_PATH` | Path to SQLite DB file | e.g. `shared/sql/mate.db` |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL / MySQL credentials | Required if `DB_TYPE` != `sqlite` |
| `GOOGLE_API_KEY` | Google Gemini API key (primary LLM) | Required for Gemini models |
| `OPENROUTER_API_KEY` | OpenRouter API key | Required if routing through OpenRouter |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Additional provider keys via LiteLLM | Optional |
| `MODEL_TYPE` | Primary model provider format | `gemini` or `litellm` |
| `AUTH_USERNAME` / `AUTH_PASSWORD` | Dashboard HTTP Basic Auth credentials | Default: `admin` / `mate` |
| `MATE_ENV` | Operational environment | `development` (default) or `production` |
| `MATE_ALLOW_INSECURE_DEFAULTS` | Override production startup security checks | `false` |
| `ALLOWED_ORIGINS` | Comma-separated CORS allowlist | e.g. `http://localhost:3000` |
| `TRUSTED_PROXY_HOSTS` | Proxy hosts trusted for `X-Forwarded-*` headers | Optional |
| `OAUTH_ALLOWED_DOMAINS` / `OAUTH_ALLOWED_EMAILS` | Restrict SSO login domains/emails | Unset allows any provider user |
| `TOKEN_TTL_HOURS` | Personal Access Token / Bearer token lifetime | `24` |
| `WIDGET_ORIGIN_STRICT` | Enforce widget key origin allowlist strictly | `false` |
| `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET` | Allow code executor on widget-linked agents | `false` |
| `WIDGET_LEGACY_ADMIN_KEY` | Transitional support for widget key on admin API | `false` |
| `ADK_HOST` / `ADK_PORT` | ADK server address | `127.0.0.1:8001` |
| `ARTIFACT_SERVICE` | Artifact storage provider | `local_folder`, `supabase`, `s3` |
| `EMBEDDING_MODEL` | Model for memory block semantic search | `gemini/gemini-embedding-001` |
| `RATE_LIMIT_ENABLED` | Enable rate limiting and budgets | `false` |
| `ALERTS_ENABLED` / `ALERTS_INTERVAL_SECONDS` | Alert rules on agent errors/bursts | `false` / `60` |
| `OTEL_TRACING_ENABLED` | OpenTelemetry distributed tracing (ADK) | `false` |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` | LangSmith run tracing (LangGraph) | `false` |
| `AUDIT_RETENTION_DAYS` | Retention period for audit logs | e.g. `365` |
| `MATE_AI_DISCLOSURE` | Custom default text for Art. 50 disclosure | Optional |

## Testing
- Tests live in `shared/test/` with `test_` prefix
- **ALWAYS run tests as module** within activated virtual environment:
  ```bash
  # Run all tests
  python -m unittest discover -s shared/test -p "test_*.py" -v

  # Run single test module
  python -m unittest shared.test.test_agent_manager_simple -v

  # Run with test coverage
  coverage run -m unittest discover -s shared/test -p "test_*.py"
  coverage report
  ```
- Write unit tests for all new agent, tool, callback, or route functionality
- Test both successful and failure scenarios
- Use mocking for external API and LLM calls
- Include integration tests for agent hierarchies and external endpoints

## Error Handling
- Always include `try-except` blocks for database and network operations
- Log errors using the configured logger
- Provide fallback agents or safe defaults when agent initialization fails
- Return `None` for failed agent initialization rather than crashing the server
- Include descriptive error messages in logs

## Performance & Monitoring
- Cache initialized agents in `AgentManager`
- Use connection pooling for database operations
- Implement token usage monitoring for cost tracking (`token_usage_logs`)
- Optimize tool creation to avoid redundant initialization
- Ensure appropriate indexes on database tables
- Distributed tracing with OpenTelemetry (ADK runtime) or LangSmith (LangGraph runtime)
- Rule-based alerts for agent failure rates, guardrail bursts, and budget overruns

## MCP Integration
- Integrated MCP (Model Context Protocol) server at `/mcp`
- Full protocol support with Server-Sent Events (SSE) and HTTP streaming
- Agents can act both as MCP servers and consume external MCP servers
- Secure authentication headers and environment variable secret interpolation (`${VAR}`)

## Dashboard & Web Interface
- Web-based dashboard under `/dashboard/` with API endpoints under `/dashboard/api/`
- Jinja2 templates in `templates/` and static assets in `static/`
- Vue-style JavaScript with no build step required; PWA-ready (`static/manifest.json`)
- **Work Room & Code Canvas**: Side-by-side chat with live code canvas supporting Ace editor, sandboxed HTML/JS/CSS iframe execution, Pyodide Python, and DartPad
- Public embeddable chat widget (`/widget/`) and standalone chat (`/standalone/`)
- Agent management UI requires selecting a project before listing or editing agents

## Important Documentation
- `README.md` — Project overview, architecture, and quick start
- `documents/` — In-depth feature guides:
  - `documents/AI_ACT.md` — EU AI Act compliance guide (Art. 50, Art. 50(2), Art. 12)
  - `documents/EXTERNAL_AGENTS.md` — External OpenAI-compatible agent endpoint setup
  - `documents/MCP_SERVERS.md` — MCP server and client integration (stdio, HTTP/SSE, `${VAR}`)
  - `documents/LANGGRAPH_RUNTIME.md` — LangGraph alternative runtime setup and wire contract
  - `documents/OPENAI_COMPATIBILITY.md` — OpenAI-compatible API (`/v1/chat/completions`)
  - `documents/SLACK_INTEGRATION.md` — Slack bot integration and Block Kit card translation
  - `documents/WIDGET_INTEGRATION.md` — Embeddable widget integration and origin security
  - `documents/RATE_LIMITS.md` — Rate limiting, token budgets, and cost controls
  - `documents/ALERTS.md` — Notification rules on agent errors and budget thresholds
  - `documents/TRACING.md` — OpenTelemetry and LangSmith tracing setup
  - `documents/AGENT_WIZARD.md` — Public self-service agent builder wizard
  - `documents/TEMPLATE_LIBRARY.md` — Agent templates and presets
- `shared/sql/README.md` — Database schema reference
- **Rules**:
  - Do not create a separate README.md for each feature; place feature docs in `documents/`
  - Update `AGENTS.md` when adding new patterns or architectural requirements

## Engineering & Coding Guidelines

### 1. Think Before Coding
- **Don't assume. Don't hide confusion. Surface tradeoffs.**
- Before implementing: state assumptions explicitly; if uncertain or multiple interpretations exist, ask or present options.
- If a simpler approach exists, propose it.

### 2. Simplicity First
- **Minimum code that solves the problem. Nothing speculative.**
- Avoid unnecessary abstractions for single-use code.
- No unrequested "flexibility" or premature configurability.
- If 50 lines cleanly solve a problem, do not write 200 lines.

### 3. Surgical Changes
- **Touch only what you must. Clean up only your own mess.**
- Do not refactor unrelated code or adjust untouched comments/formatting.
- Match existing style and idioms in the file.
- Clean up unused imports/variables created by your changes, but do not delete pre-existing dead code unless requested.
- Every changed line should trace directly to the task.

### 4. Goal-Driven Execution
- **Define success criteria. Verify thoroughly.**
- Write or run unit tests to confirm bug fixes and new functionality.
- For multi-step tasks, define a concise verification plan and execute step by step.
