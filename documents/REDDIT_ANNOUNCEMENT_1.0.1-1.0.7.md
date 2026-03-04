# MATE v1.0.1 → v1.0.7 – Reddit announcement

We've been shipping updates to **MATE (Multi-Agent Tree Engine)** based on feedback—here's a concise rundown of what's landed from **v1.0.1** through **v1.0.7**: embeddable widgets, versioning, guardrails, tracing, rate limits, templates, audit trail, and a responsive dashboard. Details below.

*Follow-up to the [original MATE intro post](https://www.reddit.com/r/agentdevelopmentkit/comments/1rhcqbh/mate_opensource_multiagent_tree_engine_for_google/).*

---

**v1.0.1** – Embeddable chat widget (iframe, API keys, origin limits), widget admin panel, session isolation, "New Chat" fix, SSE filtering, inline "Thinking…", light/dark/auto theme.

**v1.0.2** – Agent config versioning with full history, Monaco diff, one-click rollback, custom version tags.

**v1.0.3** – Guardrails: PII detection/redaction, prompt-injection detection, content blocklists, output length limits, guardrail logs and dashboard UI.

**v1.0.4** – OpenTelemetry tracing (turns, LLM, tools), GenAI spans, W3C propagation, dashboard trace viewer, optional DB storage, OTLP export. Off by default, zero overhead when disabled.

**v1.0.5** – Rate limits and token budgets (per user/agent/project), dashboard config and usage gauges, webhook alerts, 429 + Retry-After. Opt-in via env.

**v1.0.6** – Template library: pre-built agent configs, gallery at `/dashboard/templates`, one-click import, community templates via PR.

**v1.0.7** – Audit trail (EU AI Act): append-only `audit_logs` (config changes, user/agent CRUD, RBAC denials, login/logout, widget keys), configurable retention, dashboard at `/dashboard/audit-logs` with filters and JSON/CSV export. Responsive dashboard (mobile/tablet), hamburger nav, touch-friendly UI, PWA (manifest, service worker, install), responsive chat widget. Template CSS fix for agents page.

---

**TL;DR** – Widget embedding, config versioning & rollback, guardrails, tracing, rate limits & budgets, template library, audit trail (EU AI Act), responsive dashboard & PWA. Questions welcome.
