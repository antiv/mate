# Changelog

All notable changes to MATE (Multi-Agent Tree Engine) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

- **Multi-Agent Tree Engine** - hierarchical agent orchestration with root, sequential, parallel, and loop agent types
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
