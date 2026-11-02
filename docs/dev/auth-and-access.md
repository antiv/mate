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
  - shared/utils/auth_utils.py
  - server/proxy_routes.py::_non_admin_refusal
  - shared/utils/dashboard/dashboard_server.py::_require_in_workspace
  - shared/utils/dashboard/dashboard_server.py::_check_agent_names
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
an account at the provider can create a session.

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

## Layer 1: the dashboard API

`DashboardAuthzMiddleware` requires an administrator for every request under
`/dashboard/api/`, reads included. The exceptions are two short allowlists in
`server/dashboard_authz.py`, for what the Work Room and the account menu need:
renaming a conversation, rating a reply, reading one's own ratings, and listing,
creating and revoking one's own personal access tokens.

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
- `ctx.role` is `get_user_role`'s answer, `admin` or `user`, and `ctx.is_admin`
  says whether it is `admin`. It is read the first time a route asks, since for a
  single sign-on user it is a database lookup.

Every `/dashboard` route takes it, and the route inventory test fails on one that
does not.

### The workspace of a request

The context also carries the workspace the caller works in, and a route filters by
it, never by a workspace id from the query (`documents/design/workspaces.md`, D6):

- `ctx.workspace_id` is the workspace named by the `X-MATE-Workspace` header, or
  else the caller's default: workspace 1 for a platform administrator, else the
  open workspace with the lowest id they are a member of. A user with no membership
  at all, a pending sign-up, gets workspace 1 with no role.
- `ctx.workspace_role` is the caller's role there (`owner`, `admin`, `member`), or
  `None` for a platform administrator who isn't a member.
- `ctx.is_platform_admin` is what `ctx.is_admin` means today: `get_user_role`
  answers `admin`. Platform administrators may open every workspace.
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

Until every route is scoped, `ctx.is_admin` stays the platform administrator only,
so a workspace's own owner or admin can't reach a route that doesn't filter yet.

`shared/test/test_cross_workspace.py` proves the scoping on the real app, with two
workspaces. A platform administrator in workspace A calls every `/dashboard/api/`
GET route, and none may answer with B's data. Every route that names a row in its
path is called with B's ids, and must answer 404. A write that names no row needs a
test of its own, named in `COVERED_BY_TEST`; one that names a row in its body is
listed in `BODIES_NAMING_B`, with a body naming B's row, and must answer 404. Routes
listed in `NOT_YET_SCOPED` (phase 2 empties it), `PLATFORM_ONLY` (installation-wide)
and `SHARED` (the template catalogue) are left out, so a new route is checked as
soon as it is added.

An API route that is for admins and is reachable by others, because it is on an
allowlist or outside `/dashboard/api/`, checks `ctx.is_admin` itself and answers
403.

Dashboard pages check `ctx.is_admin` and redirect non-admins to the Work Room.

## Layer 2: the proxy to the agent server

The agent server trusts whatever user id it is given, so the proxy enforces it. For
a non-admin, `_non_admin_refusal` allows only:

- `list-apps`
- paths under `apps/<app>/users/<id>/` where `<id>` is the caller's own
- `run` and `run_sse` when the user id in the body is the caller's own. ADK reads a
  body field in snake_case or camelCase and prefers `userId` when both are present,
  so both spellings are checked, and a body where they differ is refused
  (`server/run_body.py`). The rate-limit middleware reads `appName` and `userId` the
  same way and answers `400` to a body whose spellings disagree.

Everything else the agent server offers is admin-only. The `/run_live` WebSocket
applies the same rule.

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
- a widget visitor (`widget_<key id>_...`) may only in the workspace of their key's
  project, and `trigger_runner` in any, both matched on their `users.roles`;
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
| A dashboard page | Take `ctx: RequestContext = Depends(get_request_context)`; redirect to the Work Room unless `ctx.is_admin`, if it is for admins. |
| Dashboard JSON for admins | Put it under `/dashboard/api/` and take `ctx: RequestContext = Depends(get_request_context)`. The middleware does the rest. Filter by `ctx.workspace_id`, and answer 404 for a row of another workspace; the cross-workspace test checks it. |
| Dashboard JSON for every signed-in user | The same, plus an allowlist entry and its `USER_ROUTES` line in the route inventory test, plus scoping to the caller in the handler. |
| For external programs | Depend on `get_pat_user` (PAT with role check) or `get_auth_user` (bearer or Basic). |
| Public | No dependency. Think about rate limiting, and about what the response reveals. |
