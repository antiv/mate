# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MATE (Multi-Agent Tree Engine) is a production-ready web platform built on top of Google ADK that adds database-driven agent management, multi-LLM support, RBAC, MCP integration, token tracking, guardrails, and a dashboard UI.

## Running the Application

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Set GOOGLE_API_KEY or other LLM provider key

# Run (auto-applies DB migrations, default SQLite)
python auth_server.py
# Dashboard: http://localhost:8000  (default login: admin/mate)

# Docker alternative
docker-compose up
```

Two servers run: **Auth Server** (port 8000, FastAPI with HTTP Basic Auth) proxies authenticated requests to the **ADK Server** (port 8001, Google ADK runtime).

## Testing

```bash
# All tests
python -m unittest discover -s shared/test -p "test_*.py" -v

# Single test file
python -m unittest shared.test.test_agent_manager_simple -v

# With coverage
coverage run -m unittest discover -s shared/test -p "test_*.py"
coverage report
```

Tests live in `shared/test/` — 134 tests covering agent management, tool factory, model switching, RBAC, migrations, guardrails, tracing, and more.

## Database Migrations

```bash
python shared/migrate.py run       # Apply pending migrations
python shared/migrate.py status    # Check migration status
python shared/migrate.py create    # Create new migration
python shared/migrate.py rollback  # Roll back last migration
```

Migrations auto-apply on server startup. Per-database migration files are in `shared/sql/migrations/{postgresql,mysql,sqlite}/`.

## Architecture

### Dual-Server Design
- `auth_server.py` — FastAPI server handling auth, dashboard routes, and proxying to ADK
- `adk_main.py` — Google ADK web server with agent runtime (internal, not exposed directly)
- `server/` — Auth, proxy, widget, and rate-limit middleware

### Agent System
Agents can be **database-driven** (configured via dashboard, stored in `agents_config` table) or **hardcoded** (code in `agents/` subdirectories, each with an `agent.py`). `shared/utils/agent_manager.py` merges both and builds the agent tree at startup.

### Tool Factory
`shared/utils/tools/tool_factory.py` constructs tool sets from database config. Includes MCP tools, image generation, Google Drive, file search, memory blocks, and the self-building `create_agent_tool` that lets agents create/modify other agents at runtime.

### Callbacks & Middleware
`shared/callbacks/` hooks into the ADK request lifecycle for RBAC (`rbac_callback.py`), token tracking (`token_usage_callback.py`), and guardrails (`model_guardrail.py`, `function_call_guardrail.py`). Rate limiting is in `server/rate_limit_middleware.py`.

### MCP Integration
`shared/utils/mcp/` exposes agents as MCP servers and implements MCP client consumption. Agents can both serve and consume MCP protocol tools.

### Database Layer
SQLAlchemy ORM models in `shared/utils/models.py`. Supports SQLite (default), PostgreSQL, and MySQL via `DB_TYPE` env var. Key tables: `agents_config`, `projects`, `users`, `token_usage_logs`, `guardrail_logs`, `audit_logs`, `rate_limit_config`, `memory_blocks`, `widget_api_keys`, `agent_config_versions`.

### Frontend
Dashboard templates in `templates/dashboard/`, static assets in `static/`. Vue-style JavaScript with no build step required. PWA-ready with `static/manifest.json`.

## Code Style

Follow PEP 8 with type hints on all function signatures. Use f-strings for formatting. Import order: stdlib → third-party → local. No linting config is committed — use standard PEP 8 tooling.

## Key Environment Variables

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Google Gemini (primary LLM) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc. | Additional LLM providers via LiteLLM |
| `DB_TYPE` | `sqlite` (default), `postgresql`, `mysql` |
| `AUTH_USERNAME` / `AUTH_PASSWORD` | Dashboard login |
| `ADK_HOST` / `ADK_PORT` | ADK server address (default `127.0.0.1:8001`) |
| `ARTIFACT_SERVICE` | `local_folder`, `supabase`, or `s3` |
| `RATE_LIMIT_ENABLED` | Enable per-user/agent/project budgets |
| `OTEL_TRACING_ENABLED` | OpenTelemetry distributed tracing |
| `AUDIT_RETENTION_DAYS` | EU AI Act audit log retention |

## Important Documentation

- `AGENTS.md` — Architecture patterns and development guidelines (read before adding new agents or tools)
- `documents/` — Feature-specific docs: `MCP_SERVERS.md`, `RATE_LIMITS.md`, `TRACING.md`, `WIDGET_INTEGRATION.md`, `TEMPLATE_LIBRARY.md`, `AGENT_WIZARD.md`
- `shared/sql/README.md` — Database schema reference


Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

