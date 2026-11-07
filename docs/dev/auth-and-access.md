---
title: Authentication and access control
summary: Every way a caller proves who they are, and the separate layers that decide what they may do.
audience: dev
order: 40
covers:
  - server/auth.py
  - server/auth_routes.py
  - server/oauth_routes.py
  - server/pat_auth.py
  - server/dashboard_authz.py
  - server/request_context.py
  - server/workspace_routes.py
  - shared/utils/platform_only.py
  - shared/utils/auth_utils.py
  - server/proxy_routes.py::_non_admin_refusal
  - shared/utils/dashboard/dashboard_server.py::_require_in_workspace
  - shared/utils/dashboard/dashboard_server.py::_check_agent_names
  - shared/utils/dashboard/dashboard_server.py::_traces_in_workspace
  - shared/utils/dashboard/dashboard_server.py::_require_scope_in_workspace
  - shared/utils/dashboard/dashboard_server.py::_refuse_platform_only
---

# Authentication and access control

Access in MATE is decided in layers. Authentication establishes an identity. Then
three independent checks apply, depending on what is being reached: the dashboard
API, the agent server behind the proxy, and the agent itself.

## Ways to authenticate

| Caller | Credential | Checked in |
|---|---|---|
| Built-in account | HTTP Basic, or the session opened by the login page | `server/auth.py` |
| Single sign-on user | Encrypted session cookie, set after Google or GitHub login | `server/oauth_routes.py` |
| Script or service | Bearer token from `POST /auth/token` | `shared/utils/auth_utils.py` |
| OpenAI-compatible client | Personal access token (`mate_pat_...`) | `server/pat_auth.py` |
| Website visitor | Widget key (`wk_...`) in `X-Widget-Key` | `server/widget_routes.py` |
| Webhook sender | Trigger fire key, optionally an HMAC signature | see [Trigger engine](trigger-engine.md) |
| Slack | Slack's request signature | `server/slack_routes.py` |

For dashboard routes, `get_dashboard_auth_user` tries, in order: the session cookie,
a bearer token, Basic credentials, then the legacy `auth_token` cookie. It returns
the identity or `None`; it never triggers the browser's Basic-auth dialog.

### Bearer tokens

`POST /auth/token` returns a random token valid for `TOKEN_TTL_HOURS` (24 by
default). Tokens are kept in the memory of the auth server process: a restart
invalidates them all, and a second worker process would not recognise them. A valid
token is treated as the built-in administrator.

### Single sign-on

Google uses OIDC with the authorization code flow and PKCE; GitHub uses OAuth 2.0. A
provider is enabled when its client id and secret are set. The redirect URIs to
register with the provider are `/auth/callback/google` and `/auth/callback/github`.

On callback, `_upsert_oauth_user` creates or updates the `users` row. A new user gets
`OAUTH_DEFAULT_ROLE`, which is `pending` unless changed. `OAUTH_ALLOWED_DOMAINS` and
`OAUTH_ALLOWED_EMAILS` restrict who may sign in at all; with neither set, anyone with
an account at the provider can create a session. An id the server gives out on its
own, one starting with `widget_` or `trigger_runner`
(`shared/utils/workspaces.py::is_reserved_user_id`), is refused at sign-in, and
**Users** refuses to create one: an address such as `widget_7_x@example.com` would
otherwise pass for a widget visitor.

The session cookie is signed with `SECRET_KEY`. Without one, a random key is
generated at startup and every session dies on restart.

### Personal access tokens

Users create PATs from the account menu. Only the SHA-256 hash is stored. A PAT
authenticates the `/v1` routes, and only for users holding one of the roles in
`ALLOWED_API_ROLES` (default `admin`, `developer`). A PAT can carry an expiry.

The same rule (`roles_allow_api` in `server/pat_auth.py`) decides who may create
one: the menu shows **Personal Tokens** only to admins and users with such a
role, and `POST /dashboard/api/tokens` refuses anyone else with 403. Listing and
revoking one's own tokens needs no role, so a user who lost theirs can still
revoke them through the API.

A token works in its own workspace (`personal_access_tokens.workspace_id`): `/v1/models`
lists that workspace's agents, a completion for another workspace's agent is a 404,
and MCP allows only that workspace's agents. A token whose owner is no longer a
member of its workspace is refused with 403, unless the owner is a platform
administrator. `get_pat_user` records the workspace and routes read it from the
`pat_workspace` dependency. Basic auth, the dashboard's bearer token and a session
work in the workspace `X-MATE-Workspace` names, or the caller's default (see
[The workspace of a request](#the-workspace-of-a-request)).

## Who is an administrator

`get_user_role` returns `admin` or `user`:

- Basic auth and bearer tokens are always `admin`.
- A single sign-on session is `admin` when its provider-verified user id or email
  equals `AUTH_USERNAME`, or when its `users` row has the `admin` role. The display
  name is deliberately not accepted, since the account holder can set it freely.

This decides dashboard access only. It is not consulted when an agent decides
whether to answer.

`admin` makes the caller a **platform administrator** (`ctx.is_platform_admin`). The
dashboard of a workspace is also open to its **owners and admins**
(`workspace_members.role`), who are no platform administrators: `ctx.is_admin` is
either. Until memberships are edited on their own (phase 3), they follow `users.roles`,
so every owner is a platform administrator too and an installation sees no
difference.

## Layer 1: the dashboard API

`DashboardAuthzMiddleware` requires an administrator (`ctx.is_admin`, of the
request's workspace) for every request under `/dashboard/api/`, reads included. The
exceptions are two short allowlists in `server/dashboard_authz.py`, for what the Work
Room and the account menu need: renaming a conversation, rating a reply, reading one's
own ratings, listing, creating and revoking one's own personal access tokens, and
listing and switching one's workspaces.

`PLATFORM_ONLY_PATHS` need a platform administrator, and answer 403 "Platform
administrator access required" to anyone else, a workspace's owner included. They act
on the whole installation (`documents/design/workspaces.md`, "What stays
platform-only"): migrations, server control, `/settings/*`, system jobs, the wizard
and shop, users, audit-log retention, creating and deleting templates, reloading every
agent, and binary builds, which pack the installation's `agents/` folder. The
patterns match by prefix, so a new route under `/settings/` or `/users` is
platform-only from the start. A request naming a workspace the caller can't open is
a 404, and one nobody signed in to is left to the route's dependency.

A route added under `/dashboard/api/` is therefore admin-only without any code in
the handler. Add it to an allowlist only after deciding a non-admin may call it,
and scope the handler to the caller's own data. A denied request is written to the
audit log as `rbac.denial`.

`shared/test/test_route_inventory.py` lists every `/dashboard/api/` route from the
app's router, those of included routers too, and requests each as a non-admin. It
fails when one gets anything but the middleware's 403, or when the allowlists let
through a route that is not in its `USER_ROUTES`. A new allowlist entry therefore
also goes into `USER_ROUTES`.

### The request context

A dashboard route learns who is calling from one dependency,
`ctx: RequestContext = Depends(get_request_context)` (`server/request_context.py`):

- `ctx.user_id` is the signed-in user. Without one, the caller is redirected to the
  login page, as `require_dashboard_auth` does. The routers in `server/` (docs,
  widget keys, chat integrations) take `get_api_request_context` instead, which
  answers 401, as they always have.
- `ctx.role` is `get_user_role`'s answer, `admin` or `user`, and
  `ctx.is_platform_admin` says whether it is `admin`. `ctx.is_admin` is a platform
  administrator, or an owner or admin of `ctx.workspace_id`. Each is read the first
  time a route asks, since for a single sign-on user it is a database lookup.

Every `/dashboard` route takes it, and the route inventory test fails on one that
does not. The context is made once a request (`context_for`, kept on
`request.state`): `DashboardAuthzMiddleware` makes it to check for an admin, and the
route's dependency reuses it and the workspace it resolved.

### The workspace of a request

The context also carries the workspace the caller works in, and a route filters by
it, never by a workspace id from the query (`documents/design/workspaces.md`, D6):

- `ctx.workspace_id` is the workspace named by the `X-MATE-Workspace` header, else
  the one the dashboard session switched to, else the caller's default: workspace 1
  for a platform administrator, else the open workspace with the lowest id they are
  a member of. A user with no membership at all, a pending sign-up, gets workspace 1
  with no role.
- The session's choice is `session["workspace_id"]`, written by `PUT
  /dashboard/api/workspaces/current` (`server/workspace_routes.py`) after checking
  that the caller may open it; `GET /dashboard/api/workspaces` lists the ones they
  may. A choice the caller has since lost, or a workspace since closed, falls back
  to the default rather than a 404 on every page. The proxy and `/list-apps` read it
  too, so the Work Room follows the switch. `base.html` shows the switcher only to a
  user with more than one workspace.
- `ctx.workspace_role` is the caller's role there (`owner`, `admin`, `member`), or
  `None` for a platform administrator who isn't a member.
- Platform administrators may open every workspace.
- A header naming a workspace that doesn't exist, is deleted, or that the caller is
  not a member of is a 404, so the answer doesn't confirm the workspace exists.

The workspace is resolved the first time a route reads it, as `ctx.role` is.
`shared/utils/workspaces.py` has the lookups a route needs, such as
`project_in_workspace`, which returns `None` for another workspace's project, and
the route answers 404.

A dashboard route that names a row, in its path or its body, calls
`DashboardServer._require_in_workspace(ctx, agent_name=..., config_id=...,
project_id=..., version_id=..., store_name=..., test_case_id=...)` before it acts.
It answers 404 unless every row named is the current workspace's. An agent name
with no `agents_config` row is a hardcoded agent, which belongs to the installation:
only a platform administrator reaches it, from the default workspace. Lists join to
the project and filter on `projects.workspace_id`. Test cases carry their own
`workspace_id`, and ratings are matched through their agent.

Agent names stay unique across the installation (`documents/design/workspaces.md`,
D1). Creating or renaming an agent to a name in use anywhere answers 409, "This
name is taken", without saying where. `parent_agents` may only name agents of the
same workspace (400), and `AgentManager.get_subagents` and LangGraph's
`_load_child_configs` look for children in the parent's workspace only, so a name
can't link two workspaces. A name with no config counts as the default workspace's.
An import (`POST /dashboard/api/agents/import`) skips an agent, block or trigger
that names another workspace's project, and an agent whose name another workspace
uses, even with `overwrite=true`. An imported trigger takes the project of its
agent. The agent tools of `create_agent_tool.py` (create, update, delete) reach
only agents of the calling agent's workspace.

The template catalogue (`templates/agent_templates/`) is shared by every workspace:
listing and reading it is open, and importing creates the project in the current
workspace. Creating a template from agents and deleting one are platform-only, so
the catalogue holds no workspace's agents.

Logs and operations filter the same way, with two helpers in `workspaces.py`:
`rows_of(model, workspace_id)` on a table's own `workspace_id`, and
`in_projects_of(column, workspace_id)` on a `project_id`. Both count a row with
none as the default workspace's: an agent with no config, or an audit entry about
the installation (a sign-in, a user change). Through them:

- the usage figures and pages, the usage, guardrail and audit logs, rated answers,
  personal access tokens (`POST` writes `ctx.workspace_id`), rate limits and alert
  rules read the workspace's rows, and a route naming another workspace's rule,
  trigger, widget key, token or integration answers 404;
- a session belongs to the workspace of its `app_name`, its root agent; detail and
  delete answer 404 for another's. ADK keys a session by app, user and id, and a
  client may choose the id, so the lookup narrows by the app and user the caller
  names, reads only that session's events, and deletes by the whole key of the
  session it checked;
- a rating (`POST /dashboard/api/feedback`) names its agent, and is refused with 404
  unless the session is the caller's (any of the workspace's for an admin) with that
  agent; the rated-answer pages read a session's events by its id and agent;
- a trace belongs to a workspace when every agent its spans name
  (`gen_ai.agent.name`, `mate.agent.name`) is one of its agents, and a trace naming
  none to the default workspace (`DashboardServer._traces_in_workspace`);
- a budget, alert rule, trigger, widget key or Slack integration may name only the
  workspace's agents and projects. `workspaces.missing_from` answers which of the
  two is another workspace's;
- `rate_limit_config` is looked up by scope, scope id and workspace, so a user has
  a limit in each workspace. The request check takes the configs of the agent's
  workspace, and counts a user's tokens and spend there only. Alert rules measure
  their own workspace (`docs/dev/alerts.md`).

A Slack team connects to one integration on the installation (`UNIQUE (team_id)`,
V047): an event names only its team. Creating or editing an integration onto a team
that is taken answers 409 without saying whose it is. The first workspace to connect
a team keeps it; the 409 tells the caller to ask a platform administrator, who can
switch to the workspace holding it and delete that integration.

### What only a platform administrator may give an agent

Some agent settings run code with the server's rights or read its environment, and
`shared/utils/platform_only.py` keeps them the platform's:

- the code executor (`tool_config.code_executor`);
- an MCP server started as a command (an `mcpServers` entry with no `url`);
- `${VAR}` in `model_api_key`, `model_base_url` or `mcp_servers_config`;
- a hardcoded agent: the `hardcoded` flag, or a name that has a folder in `agents/`,
  whose `agent.py` the agent server would load.

On save, `DashboardServer._refuse_platform_only` answers 403 when a caller who is no
platform administrator adds one of these, or changes the part of the config that
defines it (`features_added`): a new command for an existing stdio server runs new
code, and a new base URL beside a `${VAR}` key sends the variable there. For `${VAR}`
the defining part is each place that holds one, with where it is sent: the model's
key and base URL together, and each MCP server entry whole, so editing a server that
holds none is no change. The check
reads `mcp_servers_config` as the runtimes do: they decode an entry's `headers`,
`args` and `env` once more when it is a string and only then resolve `${VAR}`, so a
placeholder spelled with JSON escapes (`${VAR}`) there is seen too. A new base
URL beside a stored key the request keeps without naming it (the `__stored__`
sentinel) is refused as well, as the key would go to that host: the caller types
the key again. So a
workspace admin keeps what a platform administrator set up, as it is. Agent create,
update, rollback and template import refuse; an agent import and a template sync skip
the agent and say why (`errors`, `agents_skipped`). `_check_agent_names` answers 409
for a name with a folder in `agents/`, and deleting a hardcoded agent's folder is
platform-only.

Every path that makes agent names keeps to the same rules:

- **A new name** must be one ADK accepts, an identifier (`platform_only.is_agent_name`):
  `_check_agent_names` answers 400 otherwise on create and rename, and an import skips
  the agent. The name becomes a folder under `agents/` and a LIKE pattern, so `..`,
  `/srv` or `%` would reach outside the agent. An agent keeps a name it already has.
  Deleting an agent with `delete_folder=true` goes through `_delete_agent_folder`,
  which deletes only a folder right under `agents/` named exactly like the agent, and
  the agent server's start creates folders for valid names only.
- **Moving a root agent** to another project moves its sub-agents along: those of its
  own workspace whose `parent_agents` names it exactly, by the name it had before
  the update.

- **A clone** renames each agent to `name + suffix` and writes the new names into
  the cloned `instruction`, `mcp_servers_config`, `tool_config` and
  `guardrail_config`. So the suffix may hold only letters, digits and underscores
  (400 otherwise), or it could write `${VAR}` or JSON into the config. For a caller
  who is no platform administrator, a name with a folder in `agents/` counts as
  taken and is bumped (`_2`). A clone whose rename changes a platform-only feature of
  its source, such as a stdio server whose arguments name the agent, is refused with
  403 before any agent is written.
- **A template import** builds the names from the project name, so it computes them
  before creating the project. One with a folder in `agents/` refuses the import
  with 409. A template sync skips such a new agent ("this name is taken").

At run time, `may_run_code` refuses the code executor and stdio MCP servers to an
agent outside the default workspace, in the tool factory, `mcp_tools.py` and
LangGraph's `mcp_client.py`, until phase 3 lets a platform administrator enable them
per workspace. A config that names its `project_id` is judged by that project's
workspace: a subagent made by delegation has its parent's project and a name with no
row. An agent with no config and no project counts as the default workspace's. The
check fails closed: without the database, or with a project that doesn't exist, the
tools are refused.

`shared/test/test_cross_workspace.py` proves the scoping on the real app, with two
workspaces. A platform administrator in workspace A calls every `/dashboard/api/`
GET route, and none may answer with B's data. Every route that names a row in its
path is called with B's ids, and must answer 404. A write that names no row needs a
test of its own, named in `COVERED_BY_TEST`; one that names a row in its body is
listed in `BODIES_NAMING_B`, with a body naming B's row, and must answer 404. Routes
listed in `PLATFORM_ONLY` (installation-wide), `SHARED` (the template catalogue) and
`ABOUT_THE_CALLER` (the workspaces the caller may open) are left out, so a new route
is checked as soon as it is added. An owner of B with no admin role, signed in with a
session cookie, must get the platform-only 403 on every `PLATFORM_ONLY` route, and
`PLATFORM_ONLY` must name exactly the routes `PLATFORM_ONLY_PATHS` matches.

An API route that is for admins and is reachable by others, because it is on an
allowlist or outside `/dashboard/api/`, checks `ctx.is_admin` itself and answers
403.

Dashboard pages check `ctx.is_admin` and redirect non-admins to the Work Room. The
installation's own pages (users, settings, migrations, the wizard and shop orders)
check `ctx.is_platform_admin`. `base.html` and the pages hide their links and
controls (server status, system jobs, audit retention, Save as Template, Reload All,
the API explorer) unless the template global `is_platform_admin(request)` says so.

## Layer 2: the proxy to the agent server

The agent server trusts whatever user id it is given, so the proxy enforces it. For
a non-admin, `_non_admin_refusal` allows only:

- `list-apps`
- paths under `apps/<app>/users/<id>/` where `<id>` is the caller's own
- `a2a/<agent>/...`, an agent's A2A endpoint and card, when the caller may use the
  agent: `caller_access` for its workspace and its `allowed_for_roles`
  (`roles_allow`), checked here since A2A runs the agent under an id of its own
- `run` and `run_sse` when the user id in the body is the caller's own. ADK reads a
  body field in snake_case or camelCase and prefers `userId` when both are present,
  so both spellings are checked, and a body where they differ is refused
  (`server/run_body.py`). The rate-limit middleware reads `appName` and `userId` the
  same way and answers `400` to a body whose spellings disagree.

Everything else the agent server offers is admin-only. The `/run_live` WebSocket
applies the same rule, and builds the agent server's query with `urlencode`, so a
`session_id` cannot add a second `user_id`.

A non-admin path with a `.` or `..` segment is refused before these checks, also
when the dots are percent-encoded. The checks read the path as it was sent, and
uvicorn passes a raw `../` (or a decoded `%2e%2e`) through, but httpx resolves dot
segments when the proxy forwards the request: `apps/bot/users/<own id>/../<other
id>/sessions` would pass as the caller's own path and reach the other user's
sessions. An admin's path is forwarded as before. For the same reason the widget
escapes the visitor id when it creates a session (`_create_adk_session`).

On what passes, `_workspace_refusal` also refuses an agent of a workspace the
non-admin can't use (`caller_access`, below), before the agent server is reached.

`/list-apps` answers with the agents of the request's workspace, for everyone: the
one `X-MATE-Workspace` names, else the caller's default. An app with no
`agents_config` row is listed to platform administrators in the default workspace.
Both the plain list and `?detailed=true` are filtered.

## Layer 3: the agent

Before each model call, the role check
(`shared/utils/rbac_middleware.py::check_agent_access`) takes the agent's workspace
from its project, and asks `shared/utils/workspaces.py::caller_access` whether the
caller may use that workspace's agents:

- a platform administrator (the `admin` role) may, and passes `allowed_for_roles`;
- an owner or admin of the workspace may, and passes `allowed_for_roles`;
- a member may, matched on their membership's `agent_roles`;
- a widget visitor (a `widget_<key id>_...` id with the `widget` role, which only the
  server gives out) may only in the workspace of their key's project, and
  `trigger_runner` in any, both matched on their `users.roles`. An id of that shape
  without the role is matched as anyone else;
- anyone else may not, and gets "This agent is not available", as for an unknown
  agent. So does a caller for an agent with no config, unless a platform
  administrator.

A caller matched on roles needs one of the agent's `allowed_for_roles`; an empty
list is admin-only.

A user id seen for the first time gets a row with the role `user`, or `widget` when
the id starts with `widget_` (`shared/utils/user_service.py::get_or_create_user`). The
check creates it as a member of the agent's workspace, so an end user an API client
runs through `/run_sse`, an A2A or a Slack user joins that workspace, not the
default one. A widget visitor is no member, and `trigger_runner` joins the default
workspace. Since users are installation-wide, an id that is already a member of
another workspace is refused on this one's agents: a client of a second workspace
should give its end users ids of their own.
The id equal to `AUTH_USERNAME` gets `admin, user`, and an existing row for it that
lacks `admin` has it added on the next lookup, so the built-in account passes the
check for an empty list. An OAuth user whose id equals `AUTH_USERNAME` is treated the
same way, as it already is by `server/auth.py` and `server/pat_auth.py`.

The check runs again for each sub-agent the conversation is transferred to.

The Work Room lists the root agents of the current workspace the caller may use
(`roles_allow`), and the MCP listing the agents of the token's workspace the caller
may use.

It refuses when it can't do its job: no user id in the context, no agent name, or no
enabled `agents_config` row for the agent. The last also covers a disabled agent and
a lookup that failed. An error during the check refuses too.

- The LangGraph hook (`shared/utils/langgraph/hooks.py::check_rbac`) refuses an agent
  without a config the same way. Its `/run_sse` already rejects a request without a
  user id or app name. It runs at the start of each turn for the agent requested
  and, after a transfer, for the sub-agent the turn resumes in, and on each
  transfer. A transfer with no run context to check is refused.
- Eval and Playground runs skip the check (`is_eval_run`), since an admin started
  them. So does the fallback agent that reports a configuration that could not load
  (`shared/utils/fallback_agent.py`). It is recognised by its own class, which the
  copy ADK runs keeps, not by its name, which any `agents_config` row can take.
- Agents built in code, outside `agents_config`, get the per-agent callbacks only if
  they attach them. With `MATE_PLUGINS_ENABLED=true` the check runs for every LLM
  agent in the app, so such an agent needs an `agents_config` row, or it is refused.

## Production checks

With `MATE_ENV=production`, the auth server refuses to start when `AUTH_PASSWORD` is
still the default, when `SECRET_KEY` is missing, or when the database cannot be
reached, and it forces the session cookie to be HTTPS-only. An empty
`AUTH_USERNAME` or `AUTH_PASSWORD`, as a copied `.env.example` has, counts as
unset (`admin` / `mate`), so the check catches it; `credentials_match` never
accepts an empty password.
`MATE_ALLOW_INSECURE_DEFAULTS=true` turns the first two back into warnings.

## Adding a route: which protection applies

| The route is | Use |
|---|---|
| A dashboard page | Take `ctx: RequestContext = Depends(get_request_context)`; redirect to the Work Room unless `ctx.is_admin`, if it is for admins, or `ctx.is_platform_admin`, if it is about the whole installation. |
| About the whole installation | Under a prefix `PLATFORM_ONLY_PATHS` matches, or a new pattern there, and a line in the cross-workspace test's `PLATFORM_ONLY`. |
| Dashboard JSON for admins | Put it under `/dashboard/api/` and take `ctx: RequestContext = Depends(get_request_context)`. The middleware does the rest. Filter by `ctx.workspace_id`, and answer 404 for a row of another workspace; the cross-workspace test checks it. |
| Dashboard JSON for every signed-in user | The same, plus an allowlist entry and its `USER_ROUTES` line in the route inventory test, plus scoping to the caller in the handler. |
| For external programs | Depend on `get_pat_user` (PAT with role check) or `get_auth_user` (bearer or Basic). |
| Public | No dependency. Think about rate limiting, and about what the response reveals. |
