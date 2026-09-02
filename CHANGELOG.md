# Changelog

All notable changes to MATE (Multi-Agent Tree Engine) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-09-02

Everything shipped since 1.0.9. Highlights: a second agent runtime (LangGraph), an
OpenAI-compatible API so external coding tools can drive MATE agents, Slack, a code canvas in
the Work Room, and a security audit.

### Added

- **LangGraph runtime** - alternative agent runtime selectable with `AGENT_FRAMEWORK=langgraph`, emulating the ADK HTTP/SSE wire contract so the dashboard, widget and API behave identically on either engine. See `documents/LANGGRAPH_RUNTIME.md`
- **OpenAI-compatible API** - `GET /v1/models` and `POST /v1/chat/completions` (streaming and non-streaming) expose root agents as standard LLM models to OpenCode, Continue, Cline and other tools. Authenticated with Personal Access Tokens. See `documents/OPENAI_COMPATIBILITY.md`
- **Slack integration** - channel and direct-message support, agent invocation, Block Kit translation of agent rich cards, and interactive button callbacks. See `documents/SLACK_INTEGRATION.md`
- **Work Room and Canvas** - built-in chat in the dashboard with a side-by-side code canvas: Ace editor, sandboxed iframe execution for HTML/JS/CSS/SVG, Python via Pyodide, and zero-install Dart/Flutter via DartPad. Canvas edits are fed back into the next prompt
- **Browser automation tools** - async Playwright tools with session management
- **Human-in-the-loop tool confirmation** - agents can require explicit approval before a tool call proceeds
- **Semantic memory search** - configurable embeddings over memory blocks with lazy backfill (`EMBEDDING_MODEL`)
- **Alerts** - rule-based notifications on agent errors, guardrail bursts and budget thresholds (`ALERTS_ENABLED`). See `documents/ALERTS.md`
- **Response quality metrics** - thumbs up/down feedback, per-response latency measurement, and tokens per conversation, with a traffic-origin filter on the latency panel
- **Agent tree cloning** - copy a root agent and its whole hierarchy into another project, renaming every agent to keep names globally unique
- **Trigger payloads** - webhook triggers pass the request body into the prompt via `{{ payload }}` / `{{ payload.field }}` placeholders, with a size cap
- **Session management dashboard** - list and filter active ADK and LangGraph sessions
- **Public agent builder wizard** - embeddable self-service wizard with provisioning, trial chat and lead capture, plus multi-tenant pricing and partner administration. See `documents/AGENT_WIZARD.md`
- **Local LLM servers** - LM Studio, llama.cpp, LocalAI and Llamafile alongside the existing hosted providers
- **File handling** - PDF and text extraction with per-model capability validation
- **Widget** - custom button icons and agent avatars, live appearance preview, copy/download on messages, a minimize control, authenticated artifact proxy with client-side caching, and mobile layout fixes
- **Agent debug mode** and required-instruction enforcement
- **Murder mystery demo** - game engine and agent templates, with case generation that misleads rather than pointing straight at the culprit. See `documents/DEMO_MURDER_MYSTERY.md`
- **Design system** - shared visual tokens applied across dashboard templates, redesigned login page with theme toggle, mobile-responsive workspace navigation
- **CI** - GitHub Actions workflow running the full test suite on every push and pull request

### Changed

- **Workflow engine** - `sequential` and `parallel` agent types are replaced by a unified graph-based workflow engine with proper loop support and error propagation. Existing agent configurations keep working; no migration is required
- **ADK 2.0** support
- The root path now redirects to the Work Room, which is the default landing page after login
- Rate limit service database operations decoupled into thread-safe sync methods

### Fixed

- **Usage charts** - the overview and usage analytics daily charts disagreed with each other: the window was built in local time against UTC rows, the oldest bucket was a partial day, and days with no traffic were dropped instead of plotted as zero
- **Agent performance table** - now backed by real measurements instead of estimates
- **Migrations** - `V026` creates the trigger function it depends on; the runner no longer splits SQL inside dollar-quoted blocks; two long-standing papercuts in the migration CLI
- **Audit log** - template deletion was silently failing to record; template and agent-config imports were never audited
- **Template import** - name substitution corrupted agent names that contain another agent's name as a prefix
- **Triggers** - trigger types that can never fire (`file_watch`, `event_bus`) are refused at creation instead of saving silently
- Agents built from the database without a description now warn, since an empty description makes the parent delegate poorly
- Streaming text deltas no longer duplicate or stutter
- The widget admin page preview keeps using the public key

### Security

- **Security audit** - patched path traversal, added API authorization middleware, and hardened identity and widget key management
- `code_executor` is refused on agents reachable through a widget key, since it is not a sandbox (`MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET` overrides)
- Widget origin allowlists are enforced on the chat surface, not only on admin routes
- HMAC signature verification on inbound trigger webhooks
- Crawler hardened against hidden prompt injection in fetched pages

## [1.0.9] - 2026-04-27

### Added

- **Eval Framework** - end-to-end quality measurement system for agent configs with three evaluation methods: exact match, semantic similarity (sentence-transformers with difflib fallback), and LLM-as-judge (litellm)
- **Eval Dashboard** - full CRUD UI at `/dashboard/evals`: manage test suites per agent, run individual tests, view score history as a Chart.js line chart, and compare avg_score + pass_rate across versions
- **Agent auto-invocation** - eval runs call the live agent automatically (no copy-paste of outputs required); uses a fresh ADK session per run and extracts only the final user-facing reply, skipping tool calls and sub-agent intermediate steps
- **Version-scoped eval runs** - "Run Evals" button in the Version History modal runs the full suite against the selected version and displays pass/fail counts, avg score, and pass rate inline
- **Regression alerts** - after each version suite run, if `new_avg < prev_avg − 0.05` and `EVAL_REGRESSION_WEBHOOK_URL` is set, a webhook fires with `type="eval_regression_alert"` payload; the UI also shows an inline regression warning
- **DB schema** - two new tables: `test_cases` (agent_name, version_id FK nullable, input, expected_output, eval_method, judge_model, threshold, is_active) and `eval_results` (test_case_id FK, version_id FK, actual_output, score, passed, eval_method, details, error, run_at)
- **`EvalRunner`** - pure Python class (`shared/utils/eval_runner.py`) with `exact_match_eval`, `semantic_similarity_eval`, `llm_judge_eval`, and `score_output` dispatcher
- **Hallucination guardrail** - replaced the unconditional stub in `shared/utils/guardrails/hallucination.py` with a real litellm LLM-as-judge call; fail-open on any exception; threshold configurable per agent
- **Versions dropdown in evals** - version selector in the Add/Edit test case form populates from real `agent_config_versions` records, eliminating FK violations from free-text input
- **Eval API** - 9 new REST endpoints under `/dashboard/api/evals/…` (list suites, get agent test cases, score history, create, update, soft-delete, run single, run version suite, list agent versions)
- **`V011__eval_framework.sql`** - migration files for SQLite, PostgreSQL, and MySQL

### Fixed

- **Hallucination guardrail** - was returning `triggered=False` unconditionally (stub); now uses LLM scoring with configurable threshold

## [1.0.8] - 2026-03-09

### Added

- **Agent Visual Builder** - drag-and-drop React Flow canvas at `/dashboard/agents/visual`. Create agents, draw parent→child connections, and manage the full hierarchy without editing JSON or Python
- **Visual Builder — Tool Settings panel** - click any tool node (or the "Configure" button in the Agent Configuration panel) to open the Tool Settings modal directly from the canvas; active tools displayed as pills
- **Visual Builder — MCP panel** - each MCP server rendered as a clickable node and as a row in the Agent Configuration panel; clicking navigates to the inline MCP Settings panel for that server; "Add" button opens the MCP Server form
- **Visual Builder — File Search panel** - "Manage" button in Agent Configuration panel pre-fills the agent context and opens the File Search (RAG) modal for store and document management
- **Visual Builder — Memory Blocks button** - "Manage" button appears in Agent Configuration panel when the `memory_blocks` tool is enabled, opening the Memory Blocks modal for that agent
- **Visual Builder — Import / Export** - one-click JSON export and file-based import of entire agent hierarchies per project
- **Config modal minimum heights** - all config modals (Tool, MCP, Planner, GenerateContent, InputSchema, Guardrail, OutputSchema) now have `min-h` so forms are fully visible on open

### Fixed

- Removed unused `childrenByName` Map from `buildGraph()` (dead code since hierarchy rendering uses depth-first parent traversal)

## [1.0.7] - 2026-03-03

### Added

- **Audit trail (EU AI Act)** - append-only `audit_logs` table: who changed what config when, user/agent CRUD, RBAC denials, login/logout, widget key management. Immutable log with configurable retention (`AUDIT_RETENTION_DAYS`). Dashboard viewer at `/dashboard/audit-logs` with filters (actor, action, resource, date range) and JSON/CSV export for compliance reporting.
- **Responsive Dashboard** - mobile (375px+) and tablet (768px+) support for all dashboard pages
- **Mobile Navigation** - hamburger menu with slide-out drawer for sidebar on small screens
- **Touch-Friendly UI** - 44px min tap targets, responsive tables with horizontal scroll
- **PWA Support** - Web App Manifest, service worker for offline shell, home screen install
- **PWA Icons** - 192x192 and 512x512 icons in `static/icons/`
- **Responsive Chat Widget** - 100dvh viewport, larger send button on mobile
- **Responsive Pages** - Overview, Agents, Usage Analytics with stacked layouts on mobile

### Fixed

- **Base template** - added `{% block extra_head %}` so agents page Monaco/agents.css loads correctly

## [1.0.6] - 2026-03-03

### Added

- **Template Library** - curated pre-built agent configurations (customer support, research assistant, code reviewer, content writer, Chess MATE)
- **Dashboard Template Gallery** - `/dashboard/templates` with search, categories, one-click import
- **One-click import** - creates project, agents, and memory blocks; agent names prefixed to avoid collisions
- **Community contribution** - add JSON to `templates/agent_templates/` via GitHub PR; see `documents/TEMPLATE_LIBRARY.md`
- **Template API** - `GET/POST /dashboard/api/templates`, `GET /dashboard/api/templates/{id}`

## [1.0.5] - 2026-03-03

### Added

- **Rate Limits & Budgets** - per-user, per-agent, per-project limits with configurable actions (warn, throttle, block)
- **Request rate limiting** - in-memory sliding window for requests/min (optional Redis for distributed)
- **Token budget caps** - tokens/hour, tokens/day (user/agent), tokens/month (project)
- **Dashboard Rate Limits UI** - configure limits, view usage vs limits, usage gauges
- **Budget alerts** - webhook on 80%, 90%, 100% threshold with `rate_limit_alert` event payload
- **429 responses** - clear message and `Retry-After` header when blocked
- **V008 migration** - `rate_limit_config` table for SQLite, PostgreSQL, MySQL
- **RATE_LIMIT_ENABLED** - opt-in via env var; `documents/RATE_LIMITS.md`

## [1.0.4] - 2026-03-03

### Added

- **OpenTelemetry Distributed Tracing** - structured spans for agent turns, LLM calls, tool invocations, RBAC, and memory
- **GenAI Semantic Conventions** - `gen_ai.inference` spans with operation, provider, model, token usage attributes
- **W3C Trace Context propagation** - traceparent/tracestate headers forwarded through auth proxy to ADK
- **Dashboard Trace Viewer** - `/dashboard/traces` page with trace list and call graph
- **DB Span Exporter** - optional storage of spans in `trace_spans` table for dashboard (V007 migration)
- **OTLP Export** - export to Jaeger, Grafana Tempo, Datadog, Honeycomb via `OTEL_EXPORTER_OTLP_ENDPOINT`
- **Zero overhead when disabled** - `OTEL_TRACING_ENABLED=false` (default) incurs no performance impact
- **Tracing documentation** - `documents/TRACING.md`

### Fixed

- **ADK TracerProvider integration** - adds DB exporter to ADK's provider instead of overriding (avoids "Overriding of current TracerProvider is not allowed" warning)

## [1.0.3] - 2026-03-03

### Added

- **Configurable Guardrails** - per-agent safety guardrails with input validation and output filtering
- **PII Detection** - regex-based detection of emails, phone numbers, SSNs, credit cards, and IP addresses with redaction support
- **Prompt Injection Detection** - pattern-based detection with configurable sensitivity levels (low/medium/high)
- **Content Policy Enforcement** - blocklist words and custom regex patterns for input/output filtering
- **Output Length Limits** - configurable maximum character and word count enforcement
- **Hallucination Check (stub)** - LLM-as-judge placeholder for future grounding verification
- **Guardrail Actions** - four action types per guardrail: block, warn, log, redact
- **Guardrail Logs** - dedicated `guardrail_logs` table for tracking all guardrail triggers with details
- **Guardrail Dashboard UI** - visual configuration modal with preset toggles and JSON editor
- **Guardrail Logs API** - `GET /dashboard/api/guardrail-logs` with filtering by agent, type, and action
- **V006 Migration** - adds `guardrail_config` column to `agents_config` and creates `guardrail_logs` table

## [1.0.2] - 2026-03-03

### Added

- **Agent Config Versioning** - every agent config change (create, update, rollback) is captured as a versioned JSON snapshot
- **Version History panel** - two-pane modal accessible from the edit agent form with full version list and Monaco diff editor
- **One-click rollback** - restore any previous agent configuration with automatic agent reinitialization
- **Version tagging** - label versions with custom tags (e.g. "v1-production") for easy identification
- **`agent_config_versions` table** - new DB table with V005 migrations for SQLite, PostgreSQL, and MySQL
- **Versioning API** - `GET /dashboard/api/agents/{id}/versions`, `POST .../rollback/{version_id}`, `PUT .../versions/{id}/tag`

## [1.0.1] - 2026-02-28

### Added

- **Embeddable Chat Widget** - iframe-based chat widget for embedding agents on external websites
- **Widget API Key system** - scoped API keys tied to project + agent with origin restrictions and custom config
- **Widget Admin Panel** - site admins can manage agent instructions, memory blocks, and files through the widget
- **Dashboard Widget Key management** - generate, list, toggle, delete widget keys with embed code generation
- **Widget session isolation** - each site visitor gets a unique scoped session; conversations persist across page refreshes
- **New Chat support** - users can start fresh conversations via "New Chat" button (always creates a new ADK session)
- **SSE response filtering** - intelligent client-side filtering of agent routing, tool calls, and narration from the final response
- **Inline thinking animation** - persistent "Thinking..." animation inside the response bubble during agent processing (no disappearing boxes)
- **Widget theming** - light, dark, and auto theme support with parent page integration
- **Widget documentation** - comprehensive integration guide at `documents/WIDGET_INTEGRATION.md`

### Fixed

- Widget session reuse bug — "New Chat" now always creates a fresh ADK session instead of silently reusing the previous one
- Agent response bubble ordering — responses now appear directly below the user's message
- Pre-tool narration text no longer leaks into the final chat response
- SSE deduplication — partial + complete ADK events no longer produce repeated text

## [1.0.0] - 2025-02-28

### Added

- **Multi-Agent Tree Engine** - hierarchical agent orchestration with root, graph, and loop agent types
- **Database-driven agent configuration** - create and manage agents via database without code changes
- **Project-scoped multi-tenancy** - isolated agent hierarchies per project
- **Universal LLM support** - Gemini (native), OpenAI, Anthropic, DeepSeek, Ollama (local), OpenRouter, and any LiteLLM-supported provider
- **MCP protocol integration** - agents can consume MCP tools and be exposed as MCP servers
- **Built-in MCP servers** - Image Generation (DALL-E 3, GPT Image 1, Gemini) and Google Drive
- **Dynamic Agent MCP servers** - expose any agent as an MCP endpoint for Claude Desktop, Cursor, etc.
- **A2A protocol support** - agent-to-agent communication via standard protocol
- **Persistent memory system** - dual memory: conversation history (DBMemoryService) + persistent memory blocks
- **Web dashboard** - full management interface with TailwindCSS, dark mode, Monaco editor
- **User management** - CRUD operations with role assignment
- **RBAC** - role-based access control on every agent
- **Token usage tracking** - monitors prompt, response, thoughts, and tool-use tokens per agent per session
- **Usage analytics** - dashboard with charts for token consumption, request patterns, cost analysis
- **Database migration system** - versioned migrations with checksums, rollback, auto-run on startup
- **Multi-database support** - PostgreSQL, MySQL, SQLite with cross-database migrations
- **Tool Factory system** - extensible tool creation for MCP, Google services, custom functions
- **Planner support** - PlanReActPlanner and BuiltInPlanner configurable per agent
- **Docker support** - Dockerfile and docker-compose for containerized deployment
- **Prometheus metrics** - HTTP metrics via `/metrics` endpoint
- **HTTP Basic Authentication** - with bearer token support
- **Agent import/export** - backup and restore agent configurations as JSON
- **Hardcoded agent integration** - mix database agents with Python-coded agents
- **Fallback mode** - graceful degradation when database is unavailable
