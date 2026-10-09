---
title: Deployment
summary: Run MATE locally, in Docker and in production, and package one agent as a standalone application.
audience: dev
order: 70
covers:
  - Dockerfile
  - docker-compose.yml
  - build_standalone_agent.py
  - standalone_server.py
  - server/csp.py
---

# Deployment

## Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env        # set at least one model provider key
python auth_server.py
```

The dashboard is at `http://localhost:8000`. The database is created and migrated on
first start.

The auth server starts the agent server with the interpreter it is running under, so
calling the virtual environment's interpreter by path (`.venv/bin/python auth_server.py`)
works without activating it.

## Docker

```bash
docker compose up --build
```

What the image and the compose file set up:

- Python 3.11, with Chromium installed for the browser tool.
- The process runs as a non-root user and exposes port 8000 only. The agent server's
  port stays inside the container.
- A health check on the auth server.
- `./data` and `./artifacts` mounted as volumes, for the SQLite database and for
  files agents produce.
- The `docs/` folder is copied into the image, so the Documentation page works.
- `.env` is passed to the container with `env_file`, so LLM keys and other settings
  in it reach the app. The image does not contain `.env`. Values in the compose file's
  `environment:` list take precedence over `.env`. A missing `.env` is not an error;
  this needs Docker Compose 2.24 or newer.

Things to adjust in `docker-compose.yml` before relying on it:

- `DB_TYPE` and `ARTIFACT_SERVICE` come from `.env` and default to `sqlite` and
  `local_folder`, which is what the mounted `./data` and `./artifacts` volumes serve.
  To use PostgreSQL or Supabase, set them in `.env` along with their connection
  settings. `DB_HOST` defaults to `localhost`, which inside the container is the
  container itself, so point it at your database host.
- It requires `AUTH_PASSWORD` to be set and refuses to start otherwise. `DB_PASSWORD`
  is optional and only matters for PostgreSQL or MySQL.
- The build argument `AGENTS_LIST` names the hardcoded agents to include.

## Production checklist

Set `MATE_ENV=production`. The server then refuses to start with the default
password, without a `SECRET_KEY`, or without a reachable database, and forces the
session cookie to HTTPS only.

| Setting | Why |
|---|---|
| `AUTH_PASSWORD` | The default is public. |
| `SECRET_KEY` | Signs the session cookie. Without it, sessions end at every restart. |
| `DB_TYPE=postgresql` and its connection settings | SQLite is fine for one small installation; use a server database for anything shared. |
| `ALLOWED_ORIGINS` | The CORS allowlist. The default is localhost. |
| `TRUSTED_PROXY_HOSTS` | Your reverse proxy's address. The default `*` lets any client spoof `X-Forwarded-*`, and audit logs then record the socket address instead. |
| `OAUTH_ALLOWED_DOMAINS` or `OAUTH_ALLOWED_EMAILS` | With single sign-on enabled and neither set, anyone with a Google or GitHub account can create a session. |
| `RATE_LIMIT_ENABLED=true` | Limits and budgets are not applied otherwise. |
| `WIDGET_ORIGIN_STRICT=true` | A widget key's allowed origins are only logged otherwise. |
| `CSP_MODE=enforce` | The Content-Security-Policy is report-only by default. Watch the reports first, then enforce. |
| `ALERTS_ENABLED=true` | Alert rules are not evaluated otherwise. |
| `AUDIT_RETENTION_DAYS` | Entries are kept forever by default. |

The [configuration reference](../reference/configuration.md) lists every variable.

### One process

Run the auth server as a single process. Bearer tokens, request counters and the
cron scheduler live in its memory: a second worker would reject the first one's
tokens and fire every scheduled trigger twice. `python auth_server.py` already runs
one process. Several instances behind a load balancer are not supported while cron
triggers are in use, since each instance would fire them; `REDIS_URL` shares the
request counters only.

### Behind a reverse proxy

Terminate TLS at the proxy and forward to port 8000. Chat responses are
server-sent events and the Work Room's browser panel uses a WebSocket, so the proxy
must not buffer responses and must allow connection upgrades. Allow long-lived
requests: a webhook trigger answers only when its agent has finished.

### First sign-in

The built-in account (`AUTH_USERNAME`) gets the `admin` role on its first chat, so
agents with an empty Allowed Roles list admit it; see
[Authentication and access control](auth-and-access.md#layer-3-the-agent).

## Standalone build

A standalone build packages one agent tree as a desktop application: a small chat
server with no dashboard and no login, for handing an agent to someone who will not
run MATE.

```bash
python build_standalone_agent.py my_agent.json
cd build/standalone
python standalone_server.py       # try it; opens http://localhost:8080
```

`my_agent.json` is an export from the Agents page. The build folder contains the
server, a SQLite database with the agents, a `.env` to fill with model keys, and a
PyInstaller spec for producing the binary. **Download Binary** on the Agents page
runs the same build in the background.

What carries over and what does not:

- Triggers are bundled, and cron triggers run in the standalone server.
- The AI disclosure is shown; `MATE_AI_DISCLOSURE` sets its wording.
- MCP servers that are started as local commands (for example through `npx`) need
  that command installed on every machine that runs the build.
- There is no authentication. Anyone who can reach the port can use the agent, so it
  listens on `127.0.0.1` by default (`STANDALONE_HOST`, `STANDALONE_PORT`).
- 👍/👎 ratings are shown only when the build can send them to a central MATE
  (`MATE_FEEDBACK_URL`, `MATE_FEEDBACK_KEY`); see
  [Standalone build](standalone-build.md#response-ratings).
