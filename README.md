# MATE — The Command Center for AI Agents

**Stop the redeploy-to-tweak loop.**

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![Google ADK](https://img.shields.io/badge/Google-ADK-orange.svg)](https://github.com/google/adk-python)
[![LiteLLM](https://img.shields.io/badge/LiteLLM-50%2B%20providers-purple.svg)](https://github.com/BerriAI/litellm)
[![MCP](https://img.shields.io/badge/MCP-compatible-brightgreen.svg)](https://modelcontextprotocol.io/)

Created by [Ivan Antonijević](https://antonijevic.rs)

You built an agent. Now you need to tune the prompt. Swap the model. Restrict access for specific users. Figure out what it's actually costing you. And make sure yesterday's behavior still works after today's changes.

Without a control layer, every one of those is a code change, a commit, and a redeploy.

**MATE is that control layer.** Built on Google ADK, it adds everything production needs — live configuration, RBAC, cost tracking, regression testing, and an embeddable chat widget — without touching your agent code.

---

## The Problem with Agents in Production

A single agent in a notebook is easy. A hierarchy of agents serving real users is not.

- **Iteration is slow.** Tweaking a prompt means editing code, committing, and redeploying. By the time you've tested three variations you've wasted half a day.
- **Governance is messy.** Who can call the admin agent? The finance agent? You need per-agent access control, but ADK doesn't ship with RBAC.
- **Costs are invisible.** You know tokens are being spent, but you can't see which agent is the expensive one, or whether it's prompt tokens, response tokens, or reasoning tokens eating your budget.
- **Regressions are silent.** You improve one prompt and unknowingly break another agent's logic. You only find out when a user complains.

---

## Three Things MATE Gives You

### 🎨 The Studio — Build and iterate without touching code

A drag-and-drop canvas where you create agents, draw parent→child connections, and attach tools without writing JSON or Python. Change a prompt, swap a model (from Gemini to GPT-4o to a local Ollama), or restructure an entire hierarchy — and it takes effect immediately, no redeploy.

Every agent lives in the database. Every change is versioned. Roll back to any previous configuration in one click.

### 🏛️ The Control Room — Govern what runs in production

RBAC per agent means the finance team's agent is never visible to basic users, without any custom middleware. Token tracking breaks down every request into four buckets — prompt, response, thoughts, and tool-use tokens — so you can see exactly where costs come from, per agent, per hour, per user.

Add guardrails (hallucination scoring, rate limits per user/project) from the dashboard. Audit logs capture every configuration change for compliance.

### 🧪 The Lab — Know before you ship

The Eval Framework runs your agents against a test suite automatically. Write expected outputs; MATE invokes the live agent, scores the response (exact match, semantic similarity, or LLM-as-Judge), and tracks pass rates across versions as a chart.

Set a regression threshold and MATE fires a webhook if a new version scores more than 5 points below the previous one. Catch prompt regressions before users do.

---

## Quick Start

```bash
git clone https://github.com/antiv/mate.git && cd mate
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set GOOGLE_API_KEY (or any supported provider key)

python auth_server.py
# Dashboard: http://localhost:8000  — login: admin / mate
```

Or with Docker:

```bash
docker-compose up
```

Migrations run automatically on startup. Default database is SQLite.

---

## Screenshots

### Dashboard Overview
Monitor usage, active agents, system health, and request trends at a glance.

![Dashboard Overview](documents/images/overview.png)

### Agent Management
View and manage your full agent hierarchy — models, parent relationships, and status per project.

![Agent Management](documents/images/agents_tree.png)

### Agent Visual Builder
Drag-and-drop canvas for building agent hierarchies. See tool and MCP nodes attached to each agent, click to configure them inline, and create connections without touching JSON.

![Agent Visual Builder](documents/images/visual_builder.png)

### Work Room
A built-in chat interface inside the dashboard. Pick any root agent from the card grid and start a conversation — no embed code, no browser tab switching. Sessions are auto-titled and persist across page reloads. The default landing page after login.

When the agent responds with code, the canvas panel opens automatically to the right of the chat. Code never clutters the conversation — it goes straight into a full-featured editor. HTML, JavaScript, CSS, and SVG can be executed in a sandboxed iframe with one click. Python runs via Pyodide (WebAssembly) directly in the browser. Dart and Flutter applications are executed directly in the browser via a seamless, zero-install **DartPad** integration that bypasses the code view to show the interactive application immediately. The panel is resizable by dragging the divider. Any edits made in the canvas are automatically included in the next prompt, so you can ask the agent to modify its own output without copy-pasting.

![Work Room](documents/images/workroom.png)

With the **Canvas Panel**, you can view and edit the generated code in a full-featured Ace Editor, and run/execute it in a sandboxed environment with a single click:

![Canvas Code Editor](documents/images/canvas_code.png)
*Editing generated code in the Canvas Panel*

![Canvas Preview Mode](documents/images/canvas_preview.png)
*Previewing and running the generated code in a sandboxed iframe*

### Agent Configuration
Edit every aspect of an agent: model, instruction, RBAC roles, memory blocks, tools, MCP servers, planner, and schemas.

![Agent Configuration](documents/images/config_agent.png)

### Tool Configuration
Toggle built-in tools or provide custom JSON — Google Drive, Search, Image, Memory Blocks, Code Executor, File Search, and more.

![Tool Configuration](documents/images/tools_agent_modal.png)

### Chat Interface
Multi-agent chat with event tracing, tool calls, and persistent memory.

![Chat Interface](documents/images/caht_chess_exsample.png)

### Usage Analytics
Token usage trends, agent performance, activity-by-hour, and per-agent success rates.

![Usage Analytics](documents/images/usage.png)

### Token Usage Details
Drill into individual request logs — prompt / response / thought / tool-use token breakdown per request.

![Token Usage Details](documents/images/usage_details.png)

---

## Why MATE vs. raw ADK

| Challenge | Raw ADK | MATE |
|---|---|---|
| Change a prompt or model | Edit code, redeploy | Dashboard edit, instant |
| Switch LLM providers | Code change per agent | Change `model_name` in config (`ollama_chat/llama3.2`, `openai/gpt-4o`, …) |
| Access control per agent | Build your own | Built-in RBAC, no code |
| Token cost visibility | DIY | 4-type tracking + analytics |
| Regression testing | Manual | Automated eval suite with LLM-as-Judge |
| Multi-team isolation | Manual | Project-scoped agent hierarchies |
| Embed chat on a website | Not included | Single `<script>` tag |
| Agents that modify agents | Not included | `create_agent` tool, RBAC-protected |

---

## Features

**Work Room**
- Built-in chat interface in the dashboard — pick a root agent from the card grid and start talking, no embed code required
- Sessions are automatically titled (LLM-generated) and persist across page reloads
- Supports the same streaming SSE, tool-use indicators, and markdown rendering as the embeddable widget
- The default landing page after login — useful for internal teams who live in the dashboard
- **Canvas panel**: code blocks auto-open in a side panel with Ace Editor instead of rendering in chat
- Execute HTML, JavaScript, CSS, and SVG in a sandboxed iframe; run Python via Pyodide (WebAssembly) in-browser; execute Dart and Flutter apps in-browser using a zero-setup **DartPad** integration that bypasses code view to show the live app directly
- Canvas edits are automatically attached to the next prompt — iterate on code without copy-pasting
- Drag the chat/canvas divider to resize; canvas closes and resets when a new session starts

**The Studio**
- Database-driven agent management — every field editable from the dashboard, zero redeploy
- Drag-and-drop Visual Builder (React Flow) — create agents, draw parent→child edges, configure tools and MCP nodes inline
- 50+ LLM providers via LiteLLM: Gemini, GPT-4o, Claude, Llama (Ollama), DeepSeek, OpenRouter, and more
- Agent version history with one-click rollback
- Self-building agents — the `create_agent` tool lets agents create, update, and delete other agents at runtime (admin-only, RBAC-protected)
- Import/export agent configurations as JSON

**The Control Room**
- RBAC per agent: assign roles in the dashboard, no middleware to write
- Google (OIDC) and GitHub OAuth 2.0 SSO with automatic user provisioning; Basic Auth always available
- Token tracking across four types: prompt, response, thoughts, tool-use — per agent, per session, per user
- Rate limiting configurable per user/agent/project from the dashboard
- Hallucination guardrail: LLM-scored factual consistency check per response, configurable threshold
- Function call and model-level guardrails
- Audit log for every configuration change (EU AI Act retention-aware)
- Prometheus metrics at `/metrics`
- OpenTelemetry distributed tracing

**The Lab**
- Eval Framework: test suites per agent with exact match, semantic similarity, or LLM-as-Judge scoring
- Auto-invocation — evals call the live agent; no copy-pasting responses
- Score history chart across versions (Chart.js)
- Regression webhooks: fires when a new version drops more than 5 points vs. the previous

**Embeddable Widget**
- Single `<script>` tag deploys a floating chat button on any website
- Per-key RBAC, origin restrictions, and scoped user namespacing
- Widget admin panel (`/widget/admin?key=...`) for non-technical teams: edit greeting, theme, colors, memory blocks, and files without dashboard access
- Light/dark/auto theme, configurable accent color, optional file attachments
- Page context injection: widget auto-reads the embedding page's URL and title and passes it to the agent as conversation context

**OpenAI Compatibility & External Clients**
- OpenAI-compatible API bridge mapping MATE agents as standard LLM models at `/v1`
- Expose any root agent as a model with a single switch in the dashboard
- Secure access using Personal Access Tokens (PATs) hashed with SHA-256 in the database
- Integration with external coding engines, CLI tools, and IDE extensions (OpenCode, Continue, Cline/Roo Code)

**Infrastructure**
- SQLite (dev), PostgreSQL, MySQL — auto-migrations on startup
- Docker Compose with health checks
- Artifact storage: local, S3, or Supabase
- MCP: agents exposed as MCP servers (Claude Desktop, Cursor) + MCP tool consumption

---

## Setup

### Environment Variables

```bash
# Minimum required
GOOGLE_API_KEY=your_key_here          # or any supported provider

# Database (SQLite by default)
DATABASE_URL=postgresql://user:pass@localhost:5432/mate
# DB_TYPE=sqlite (default), postgresql, mysql

# Authentication
AUTH_USERNAME=admin
AUTH_PASSWORD=mate

# SSO (optional)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
SECRET_KEY=<random-32-byte-hex>       # required for SSO sessions

# Additional LLM providers (only set keys for providers you use)
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENROUTER_API_KEY=...
OLLAMA_API_BASE=http://localhost:11434

# Optional features
ROOT_AGENT_NAME=my_root_agent         # override default root agent
MCP_EXPOSED_AGENTS=agent1,agent2      # expose agents as MCP servers
RATE_LIMIT_ENABLED=true
OTEL_TRACING_ENABLED=true
AUDIT_RETENTION_DAYS=365
```

### Database Migrations

```bash
python shared/migrate.py status     # check pending migrations
python shared/migrate.py run        # apply manually (auto-runs on startup)
python shared/migrate.py create     # scaffold a new migration
python shared/migrate.py rollback   # roll back last migration
```

### Docker

```bash
docker-compose up --build           # full stack with dashboard
docker-compose up -d --build        # background
```

---

## Testing

```bash
# All tests
python -m unittest discover -s shared/test -p "test_*.py" -v

# With coverage
coverage run -m unittest discover -s shared/test -p "test_*.py"
coverage report --include="shared/utils/*,server/*,auth_server.py"
```

134+ tests covering agent management, RBAC, migrations, guardrails, tracing, token tracking, and more.

---

## Embeddable Chat Widget — One Backend, Many Sites

A single MATE instance can power AI chat across multiple websites simultaneously. Each site gets its own widget key scoped to a specific agent, with independent appearance, memory, and access rules.

```html
<!-- Drop this before </body> on any website -->
<script
  src="https://your-mate-instance.com/widget/mate-widget.js"
  data-key="wk_abc123..."
  data-server="https://your-mate-instance.com"
></script>
```

A floating chat button appears instantly. No build step, no framework dependency.

### Multi-site example

```
MATE instance
├── key: wk_aaa  →  support_agent    →  company-support.com   (English, light theme)
├── key: wk_bbb  →  sales_agent      →  company-sales.com     (dark theme, no attachments)
└── key: wk_ccc  →  docs_agent       →  docs.company.com      (RAG over product docs)
```

Each key is independent: different agent, different greeting, different allowed origins, different color scheme.

### Page context awareness

When a user opens the widget on a product page, the widget automatically reads the page URL, title, and meta description and passes them to the agent as conversation context. The agent knows which page the user is viewing without any custom integration — just enable "Inject page context" in the widget admin panel.

### Widget admin panel

Each key has a standalone admin panel at `/widget/admin?key=...`. Non-technical teams can manage appearance, greeting, memory blocks, and uploaded files without touching the main dashboard.

| Setting | Admin panel |
|---|---|
| Widget title and greeting | ✓ |
| Light / dark / auto theme | ✓ |
| Button and accent color | ✓ |
| Show / hide file attachments | ✓ |
| Page context injection toggle | ✓ |
| Agent instruction and model | ✓ |
| Memory blocks (company info, FAQs…) | ✓ |
| RAG file upload | ✓ |

> Full documentation: [documents/WIDGET_INTEGRATION.md](documents/WIDGET_INTEGRATION.md)

---

## MCP Integration

MATE exposes agents and tools as MCP servers, compatible with Claude Desktop, Cursor, and any MCP client.

```bash
# Expose specific agents as MCP servers
export MCP_EXPOSED_AGENTS=creative_agent,support_agent
```

Each exposed agent gets endpoints at `/agents/{name}/mcp/*`. Built-in MCP servers: Image Generation (`/images/mcp`) and Google Drive (`/gdrive/mcp`).

> Full documentation: [documents/MCP_SERVERS.md](documents/MCP_SERVERS.md)

---

## Dashboard Routes

| Route | Purpose |
|---|---|
| `/dashboard/workroom` | **Work Room** — chat with any agent directly in the dashboard (default after login) |
| `/dashboard` | Overview — usage stats, system health |
| `/dashboard/agents` | Agent hierarchy, configuration, Visual Builder |
| `/dashboard/users` | User management and role assignment |
| `/dashboard/usage` | Token analytics, cost breakdown |
| `/dashboard/evals` | Test suites, score history, regression tracking |
| `/dashboard/audit-logs` | Audit trail viewer |
| `/dashboard/migrations` | Database migration management |
| `/dashboard/docs` | API documentation (Swagger + ReDoc) |

---

## Additional Documentation

- **[documents/OPENAI_COMPATIBILITY.md](documents/OPENAI_COMPATIBILITY.md)** — OpenAI-compatible API bridge, PAT generation, and external client setup (OpenCode, Continue, Cline)
- **[documents/WIDGET_INTEGRATION.md](documents/WIDGET_INTEGRATION.md)** — embed code, widget admin panel, JS API, security, theming, page context
- **[documents/SSO_OAUTH.md](documents/SSO_OAUTH.md)** — Google and GitHub SSO setup, enterprise domain restrictions, session security
- **[documents/EVALS.md](documents/EVALS.md)** — eval methods, regression alerts, LLM-as-Judge configuration, API reference
- **[documents/MCP_SERVERS.md](documents/MCP_SERVERS.md)** — MCP server configuration, client setup (Claude Desktop, Cursor), protocol details
- **[documents/RATE_LIMITS.md](documents/RATE_LIMITS.md)** — per-user/agent/project rate limiting
- **[documents/TRACING.md](documents/TRACING.md)** — OpenTelemetry tracing setup
- **[AGENTS.md](AGENTS.md)** — architecture patterns and guidelines for adding new agents or tools

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and PR guidelines.

## Security

For security best practices and vulnerability reporting, see [SECURITY.md](SECURITY.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
