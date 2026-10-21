---
title: Deployment
summary: Run MATE locally, in Docker and in production, and package one agent as a standalone application.
audience: dev
order: 70
covers:
  - Dockerfile
  - docker-compose.yml
  - .github/workflows/docker.yml
  - build_standalone_agent.py
  - standalone_server.py
  - server/csp.py
  - server/widget_routes.py::*origin*
  - server/widget_routes.py::widget_chat
  - shared/migrate.py
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

### Published image

Every release tag publishes `ghcr.io/antiv/mate` for `linux/amd64` and `linux/arm64`,
tagged with the version (`1.5.0`), the minor version (`1.5`) and, for the newest
release only, `latest` (`.github/workflows/docker.yml`; see [Releases](releases.md)). The workflow first builds the image for the runner and
starts it, and pushes nothing unless it answers `/health` within three minutes.

```bash
docker run -d --name mate -p 8000:8000 \
  -e AUTH_PASSWORD=change-me -e GOOGLE_API_KEY=your-key \
  -e DB_PATH=data/my_agent_data.db -v mate-data:/app/data \
  ghcr.io/antiv/mate:latest
```

- Settings are environment variables: pass them with `-e`, or keep them in a file and
  use `--env-file .env`.
- `DB_PATH=data/my_agent_data.db` puts the SQLite database in `/app/data`, so it
  survives on the `mate-data` volume. Without it the database is in `/app` and is lost
  with the container. Mount `/app/artifacts` too if agents keep files there
  (`ARTIFACT_SERVICE=local_folder`).
- The image contains no hardcoded agents (`agents/` holds none in the repository);
  agents come from the database, including the built-in `mate_help`.
- To use the agents from an MCP client, add `-e MCP_EXPOSED_AGENTS=mate_help` (or your
  agents), sign in, create a personal access token from the account menu, and connect to
  `http://localhost:8000/agents/<agent>/mcp` with `Authorization: Bearer mate_pat_...`.
  See [Exposed MCP servers](mcp-servers.md).

### Docker Compose

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
  settings; [Artifact storage](artifact-storage.md) covers each artifact backend. `DB_HOST` defaults to `localhost`, which inside the container is the
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
| `CSP_MODE=enforce` | The Content-Security-Policy is report-only by default. Watch the reports first, then enforce. Under `enforce`, an inline script runs only with the response's nonce and no inline handler runs; see [Content-Security-Policy](content-security-policy.md). |
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

## Check a deployment

`/health` answers before anything else is known. These three checks show that the
database is migrated, an agent answers through the widget, and the widget's origin
allowlist is enforced. They need an agent the widget can use (see
[Website widget](../user/widget.md#before-you-start)) and a widget key whose
**Allowed Origins** lists one site, for example `https://allowed.example`.

### Migrations applied

Run this where the server runs, so it reads the same database settings:

```bash
python shared/migrate.py status
# in Docker: docker compose exec mate python shared/migrate.py status
```

Applied and available are the same number, and pending is 0. The start log shows
migrations only at INFO level, so this is the reliable check.

### A streamed reply from an allowed origin

```bash
curl -N "$MATE_URL/widget/api/chat" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Origin: https://allowed.example" \
  -H "Content-Type: application/json" \
  -d '{"message": "Say hello"}'
```

The response is `text/event-stream`. MATE sends the first event,
`{"session_id": ..., "debug_mode": ...}`, before the model is called, so it proves
nothing on its own. After it come events with `"partial": true`, each carrying a piece
of the answer in `content.parts[].text`, and a last one with `"partial": false`. On
the ADK runtime (the default) that last event also has `"finishReason": "STOP"`; on
the LangGraph runtime (`AGENT_FRAMEWORK=langgraph`) it has no `finishReason`. An event
with `errorCode` (`error_code` on LangGraph) means the model call failed, even though
the status is 200.

### 403 from a disallowed origin

With `WIDGET_ORIGIN_STRICT=true`, the same request from a site that is not on the
list:

```bash
curl -i "$MATE_URL/widget/api/chat" \
  -H "X-Widget-Key: $WIDGET_KEY" \
  -H "Origin: https://not-allowed.example" \
  -H "Content-Type: application/json" \
  -d '{"message": "Say hello"}'
```

returns `403` with `{"detail":"Origin not allowed for this widget key"}`. If it is
answered instead, and the log shows `allowed because WIDGET_ORIGIN_STRICT is off`, the
setting has not reached the server.

The check only proves something when:

- The request sends an `Origin` header. A request with neither `Origin` nor `Referer`
  is not checked, and is answered in strict mode too.
- The key has Allowed Origins. A key without them accepts every origin.
- The disallowed origin is not MATE's own address. Requests from it always pass.

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
