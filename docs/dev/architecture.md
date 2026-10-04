---
title: Architecture
summary: The two server processes, what each owns, and the path one chat message takes through them.
audience: dev
order: 10
covers:
  - auth_server.py
  - adk_main.py
  - langgraph_main.py
  - server/proxy_routes.py
  - shared/utils/server_control_service.py
  - shared/template_agent/**
---

# Architecture

MATE is two processes. The **auth server** is everything a browser or an external
client talks to. The **agent server** runs the agents and is never exposed directly.

```
 browser / widget / API client / Slack
                │  :8000
        ┌───────▼────────────────────────────────────────────┐
        │ auth server  (auth_server.py, FastAPI)             │
        │  auth · dashboard pages + API · widget · wizard    │
        │  OpenAI-compatible API · MCP endpoints · Slack     │
        │  trigger scheduler · rate limits · CSP             │
        └───────┬────────────────────────────────────────────┘
                │  proxy, :8001 on localhost
        ┌───────▼────────────────────────────────────────────┐
        │ agent server (adk_main.py or langgraph_main.py)    │
        │  one app per root agent · sessions · artifacts     │
        │  callbacks: RBAC, guardrails, profile, token usage │
        └───────┬────────────────────────────────────────────┘
                │
        database (SQLite / PostgreSQL / MySQL) · model providers · MCP servers
```

## The auth server

`python auth_server.py` is the only command you run. On import it builds the FastAPI
app; in `__main__` it starts the agent server as a child process and then serves on
port 8000.

What it owns:

| Area | Where |
|---|---|
| Authentication and the login page | `server/auth.py`, `server/auth_routes.py`, `server/oauth_routes.py` |
| Dashboard pages and their JSON API | `shared/utils/dashboard/dashboard_server.py` (`DashboardServer`, registered on the same app) |
| Proxy to the agent server | `server/proxy_routes.py` |
| Widget, wizard, Slack, OpenAI-compatible API, docs | `server/*_routes.py`, one router each |
| MCP endpoints (images, Google Drive, exposed agents) | `shared/utils/mcp/` |
| Trigger scheduler and system jobs | `shared/utils/trigger_runner.py` |

Middleware is added in this order in the file, which means it runs in the reverse
order on a request: rate limiting (when enabled) first, then the CSP header, the
session cookie, `DashboardAuthzMiddleware`, proxy headers, and CORS.

Routers are included in a fixed order and `proxy_router` comes near the end,
because it contains the catch-all that forwards unknown paths to the agent server. A
new router must be included before it.

## The agent server

`ServerControlService.start_adk_server()` launches `adk_main.py`, or
`langgraph_main.py` when `AGENT_FRAMEWORK=langgraph`, with `--host`,
`--session-db-url` and `--a2a`, and the port in the `PORT` environment variable. The
dashboard's start, stop and restart buttons call the same service.

> The child is started with the command `python`, resolved from `PATH`, not with the
> interpreter running the auth server. If you run `.venv/bin/python auth_server.py`
> without activating the virtual environment, the agent server starts under a
> different Python and fails to import its dependencies. Activate the environment
> first.

### One app per root agent

ADK discovers apps as folders under `agents/`. Before the agent server starts,
`_initialize_agent_folders()` creates a folder for every database agent that has no
parents and is not marked hardcoded, by copying `shared/template_agent/`.

The copied `agent.py` takes its own folder name as the root agent's name, asks
`AgentManager.initialize_agent_hierarchy()` to build that agent and its sub-agents
from the database, and wraps the result in an ADK `App`. If the build fails it falls
back to a stub agent that reports the error, so one broken configuration does not
stop the server.

So a sub-agent is not an app: it is reached only through a root. Agents written in
code live in the same `agents/` folder with their own `agent.py`.

## The path of one chat message

1. The Work Room posts to `/run_sse` on port 8000 with `app_name`, `user_id`,
   `session_id` and the message.
2. The auth server authenticates the request. For a non-admin, the proxy checks that
   the `user_id` in the body is the caller's own; anything else on the agent server
   is admin-only (`_non_admin_refusal`).
3. With rate limiting on, `RateLimitMiddleware` checks the user, agent and project
   limits.
4. The proxy forwards the request to the agent server and streams the response
   back as server-sent events.
5. In the agent server, before each model call, a callback injects the user's
   profile and runs the role check and the input guardrails. A refusal ends the turn
   there.
6. The model answers or calls tools. A parent agent may transfer to a sub-agent, and
   the same callbacks run again for that agent.
7. After each model call, output guardrails run and token usage is written to
   `token_usage_logs`.

Widgets, Slack, triggers, evals and the OpenAI-compatible API all end in the same
`/run_sse` call on the agent server; they differ in how the caller is authenticated
and which `user_id` is used. See [Agent runtime](agent-runtime.md) for steps 5 to 7.

## Where state lives

| State | Where |
|---|---|
| Agents, users, projects, memory blocks, triggers, logs | The main database; see the [database reference](../reference/database.md) |
| Conversations | The agent runtime's session store. ADK and LangGraph each keep their own, so switching runtime starts with empty history. |
| Files agents produce | The artifact service: local folder, S3 or Supabase (`ARTIFACT_SERVICE`) |
| Bearer tokens, request counters, the cron scheduler | Memory of the auth server process |

The last row is why the auth server runs as a single process: a second worker would
not know the first one's tokens and would fire every cron trigger again.

## Repository map

```
auth_server.py            entry point, auth server
adk_main.py               agent server, ADK runtime
langgraph_main.py         agent server, LangGraph runtime
standalone_server.py      single-agent server for packaged builds
server/                   routers and middleware of the auth server
shared/utils/             services: agents, tools, guardrails, memory, triggers, ...
shared/callbacks/         hooks into the model and tool lifecycle
shared/sql/migrations/    schema migrations, one folder per database type
shared/test/              tests
templates/, static/       dashboard, widget and wizard front end
templates/agent_templates/  the template library
agents/                   one folder per root agent (generated) or hardcoded agent
docs/                     this documentation
```
