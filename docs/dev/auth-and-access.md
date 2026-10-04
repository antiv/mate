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
  - shared/utils/auth_utils.py
  - server/proxy_routes.py::_non_admin_refusal
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
`server/dashboard_authz.py`, for what the Work Room needs: renaming a conversation,
rating a reply, and reading one's own ratings and tokens.

A route added under `/dashboard/api/` is therefore admin-only without any code in
the handler. Add it to an allowlist only after deciding a non-admin may call it,
and scope the handler to the caller's own data. A denied request is written to the
audit log as `rbac.denial`.

Dashboard pages do their own check and redirect non-admins to the Work Room.

## Layer 2: the proxy to the agent server

The agent server trusts whatever user id it is given, so the proxy enforces it. For
a non-admin, `_non_admin_refusal` allows only:

- `list-apps`
- paths under `apps/<app>/users/<id>/` where `<id>` is the caller's own
- `run` and `run_sse` when the `user_id` in the body is the caller's own

Everything else the agent server offers is admin-only. The `/run_live` WebSocket
applies the same rule.

## Layer 3: the agent

Before each model call, the role check compares the roles on the caller's `users`
row with the agent's `allowed_for_roles`
(`shared/utils/rbac_middleware.py::check_agent_access`):

- an empty list requires the `admin` role
- otherwise the user needs any one of the listed roles

There is no bypass for dashboard administrators other than the built-in account.
A user id seen for the first time gets a row with the role `user`, or `widget` when
the id starts with `widget_` (`shared/utils/user_service.py::get_or_create_user`).
The id equal to `AUTH_USERNAME` gets `admin, user`, and an existing row for it that
lacks `admin` has it added on the next lookup, so the built-in account passes the
check for an empty list. An OAuth user whose id equals `AUTH_USERNAME` is treated the
same way, as it already is by `server/auth.py` and `server/pat_auth.py`.

The check runs again for each sub-agent the conversation is transferred to.

## Production checks

With `MATE_ENV=production`, the auth server refuses to start when `AUTH_PASSWORD` is
still the default, when `SECRET_KEY` is missing, or when the database cannot be
reached, and it forces the session cookie to be HTTPS-only.
`MATE_ALLOW_INSECURE_DEFAULTS=true` turns the first two back into warnings.

## Adding a route: which protection applies

| The route is | Use |
|---|---|
| Dashboard JSON for admins | Put it under `/dashboard/api/`; depend on `get_dashboard_auth_user` and return 401 on `None`. The middleware does the rest. |
| Dashboard JSON for every signed-in user | The same, plus an allowlist entry, plus scoping to the caller in the handler. |
| For external programs | Depend on `get_pat_user` (PAT with role check) or `get_auth_user` (bearer or Basic). |
| Public | No dependency. Think about rate limiting, and about what the response reveals. |
