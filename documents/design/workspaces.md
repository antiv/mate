# Design: workspaces (multi-tenancy)

Status: detailed design for 2.0. Tracking: #178, feature 5; first draft in #220. The
release plan is [2.0-plan.md](2.0-plan.md). Nothing in the schema changes in 1.5.

## Problem

One MATE installation serves one organisation. Projects group agents, but
everything else is shared across the installation:

- users and their roles;
- who is an administrator;
- budgets;
- the audit log;
- settings and provider keys.

An agency hosting agents for several clients, or a company with departments that
must not see each other's agents, conversations and costs, needs separate
installations today.

A **workspace** is an organisation inside one installation. It has its own members,
projects, agents, keys, budgets and audit log. A person can belong to several
workspaces.

**Not a goal for 2.0:** isolation against a hostile tenant who controls code on the
server. Workspaces share one process, one database and one agent server. Code
executor tools, MCP servers started as local processes and hardcoded agents run
with the server's rights, so they stay platform-admin features (see
[What stays platform-only](#what-stays-platform-only)).

## What the code does today

Checked against `main` at `a4dfa0b`. "dash" is `shared/utils/dashboard/dashboard_server.py`.

### Every table, by what scopes it

| Scope today | Tables |
|---|---|
| `project_id` (FK) | `agents_config` (default 1), `memory_blocks` (unique per project and label), `file_search_stores`, `widget_api_keys`, `channel_integrations`, `agent_triggers`, `response_feedback` (nullable), `wizard_sessions.trial_project_id` |
| Through a parent row | `agent_config_versions` (agent), `memory_block_versions` (block), `agent_file_search_stores` and `file_search_documents` (store), `eval_results` (test case) |
| Agent name as free text, no project | `token_usage_logs`, `guardrail_logs`, `agent_responses`, `test_cases`, `credentials.app_name`, `memory_sessions.app_name`, `lg_sessions.app_name`, `trace_spans` (inside the JSON attributes) |
| `scope` + `scope_id` strings | `rate_limit_config` (user, agent, project), `alert_rules` (user, agent, project, global) |
| User only | `personal_access_tokens`, `users.profile_data` |
| Installation-wide | `users`, `projects`, `audit_logs`, `model_prices`, `system_settings`, `wizard_config`, `wizard_partners`, `shop_orders` |
| Outside MATE's schema | ADK's `sessions`, `events`, `app_states` and `user_states`, in the session database, keyed by (app name, user id, session id) |

### Identity and access

- **One built-in administrator,** `AUTH_USERNAME`/`AUTH_PASSWORD`. Basic auth, every
  bearer token and every session that is not OAuth count as admin
  (`server/auth.py`, `get_user_role`).
  - A bearer token resolves to `AUTH_USERNAME`, so it carries no per-user identity.
- **OAuth** (Google, GitHub) creates users with `OAUTH_DEFAULT_ROLE` (`pending`).
  They are admin only with the `admin` role.
  - `OAUTH_ALLOWED_DOMAINS` and `OAUTH_ALLOWED_EMAILS` are one list for the whole
    installation.
- **Roles** are a JSON list on the user and apply everywhere. An agent's
  `allowed_for_roles` is checked against them by the RBAC callback, which finds the
  agent by name only.
- **Dashboard API.** `DashboardAuthzMiddleware` makes every `/dashboard/api/` route
  admin-only, apart from a short allowlist (Work Room titles, feedback, own tokens).
- **Other ways in:**
  - personal access tokens: OpenAI API and MCP, with the owner's roles;
  - widget keys: public, admin and feedback keys, per project;
  - trigger fire keys;
  - Slack signatures, per integration.

### The agent name is the real key

An agent's name is unique across the installation (`agents_config.name`). The same
string is all of these:

- the ADK app name and the `agents/<name>/` folder;
- `/apps/<name>`, `/a2a/<name>` and `/agents/<name>/mcp`;
- the namespace of sessions, artifacts, memory and credentials;
- the key in usage, guardrail and response logs, traces, evals, file search
  assignments, and agent budgets and alerts;
- the parent reference in `parent_agents`, which is a list of names.

The agent manager caches agents by name in one process-wide dict.
`get_subagents` finds children with `parent_agents LIKE '%"name"%'` across all
projects.

### Process-wide singletons

- Model provider keys and endpoints are environment variables (`GOOGLE_API_KEY`,
  `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `OLLAMA_HOST`, ...).
  - An agent's `model_api_key` can be a literal or `${VAR}`, and `${VAR}` only reads
    the process environment.
  - The fallback model always uses the environment.
- Embeddings, the eval judge, title generation, the image model and the wizard
  models are set per installation.
- The services: one artifact service, one memory service, one session service, one
  trigger scheduler, one rate-limit service, one alert service.
- Artifact paths are `{app}/{user}/...` with no project or tenant prefix, on all
  three backends.

## Decisions

### D1. Agent names stay unique across the installation

Renaming agents internally (`w12__support`) would change the app name everywhere:

- URLs, the session store's keys, every log row and trace, budgets;
- A2A and MCP endpoints, standalone builds, the `agents/` folders;
- existing sessions.

That is the riskiest change MATE could make, for a cosmetic gain.

Instead:

- Creating, renaming, copying or importing an agent refuses a name that exists
  anywhere: "This name is taken". The message doesn't say which workspace uses it.
- Template imports already prefix names with the project slug. Imports into a
  workspace prefix with the workspace slug when the project slug would clash.
- `parent_agents` may only name agents of the same workspace. This is checked on
  save, and `get_subagents` filters by workspace, so a name can't link two
  workspaces.

**Revisit when:** clashes become a real complaint. The internal name can be added
then as a separate project, behind the same URLs.

### D2. The workspace is copied onto log rows, not joined through the agent

`token_usage_logs`, `guardrail_logs`, `agent_responses`, `audit_logs` and
`test_cases` get `workspace_id` (and `project_id`, see the [1.6 prerequisites](2.0-plan.md#16-prerequisites)),
written when the row is written. Two reasons:

- Agents are renamed, deleted and moved between projects. A cost or an audit entry
  stays where it happened.
- Usage queries over large tables stay single-table.

`trace_spans` gets the workspace as a span attribute (`mate.workspace.id`), set where
MATE already sets `mate.agent.name`. The table keeps its shape, and replay filters by
it.

### D3. Users stay installation-wide; membership is per workspace

One person, one login, any number of workspaces. Roles move from the user onto the
membership:

```text
workspace_members
  workspace_id, user_id, role (owner | admin | member), agent_roles (JSON list)
```

- `role` decides what the person may do in the dashboard of that workspace.
- `agent_roles` takes over from today's `users.roles` for `allowed_for_roles`. In one
  workspace someone can be `finance`, and in another not.
- `users.roles` stays for the platform level: `admin` there means **platform
  administrator**.

### D4. Provider keys: shared by default, per workspace when set

This is the answer to "departments or clients":

- A workspace with no keys of its own uses the installation's environment variables,
  as today. That covers departments.
- A workspace can set its own key per provider. It is stored encrypted (D7) and
  used for every model call of that workspace's agents, the fallback model included.
  That covers clients.
- Platform administrators decide per workspace whether it may fall back to the
  installation's keys. Off for clients, so a client's agents never spend on the
  host's account.

### D5. Platform administrators create workspaces

Self-service sign-up is out of scope for 2.0. A platform administrator creates a
workspace and invites its first owner. Owners invite members.

### D6. Isolation is enforced in one place and proven by a test

- A FastAPI dependency resolves `(workspace_id, membership)` for every request.
- Routes take the filter from it, never from a query parameter.
- A test over every route proves no data crosses (see [Testing](#testing)).

### D7. Secrets at rest

Workspace provider keys, and the secrets 1.x already stores in plain columns
(`channel_integrations.bot_token`/`signing_secret`, `agents_config.model_api_key`),
are encrypted with a key from `MATE_SECRET_KEY`. That is a new required variable in
production, separate from `SECRET_KEY`, so the session secret can be rotated
without losing the stored keys.

## Data model

### New tables

```text
workspaces
  id, name (unique), slug (unique, [a-z0-9-]{3,40})
  allow_platform_keys  boolean, default true
  created_at, updated_at, deleted_at (soft delete)

workspace_members
  workspace_id → workspaces.id ON DELETE CASCADE
  user_id      → users.user_id ON DELETE CASCADE
  role         owner | admin | member
  agent_roles  JSON list, default ["user"]
  created_at, invited_by
  UNIQUE (workspace_id, user_id)

workspace_invitations
  id, workspace_id, email, role, token_hash (unique), expires_at, accepted_at, created_by

workspace_provider_keys
  workspace_id, provider (google | openai | openrouter | anthropic | ...),
  encrypted_value, base_url (nullable), updated_at, updated_by
  UNIQUE (workspace_id, provider)
```

### Changed tables

| Table | Change | Backfill |
|---|---|---|
| `projects` | `workspace_id` NOT NULL; `name` unique per workspace instead of globally | 1 |
| `token_usage_logs`, `guardrail_logs`, `agent_responses`, `audit_logs` | `workspace_id`, nullable (installation events have none); `project_id` from 1.6 | 1 |
| `test_cases` | `workspace_id` | 1 |
| `rate_limit_config`, `alert_rules` | scope `workspace` added; every row gets `workspace_id`, so the same scope id can't reach across workspaces | 1 |
| `personal_access_tokens` | `workspace_id`: a token acts in one workspace | the workspace of its owner's first membership |
| `channel_integrations` | lookup by `team_id` returns at most one row: `UNIQUE (team_id)` | duplicates are reported by the migration and must be resolved before it continues |
| `system_settings` | `workspace_id` nullable: null is the installation default, a row with a workspace overrides it (image model, later others) | none |
| `wizard_*`, `shop_orders` | unchanged: the wizard stays a platform feature | none |

Every table with `project_id` gets its workspace through the project. No column is
added there.

### The default workspace

The migration:

1. creates workspace 1, "Default", slug `default`;
2. moves every project into it;
3. adds every user as a member:
   - users with the `admin` role become `owner`;
   - everyone else becomes `member`, with `agent_roles` copied from their current
     roles minus `admin`;
4. sets `workspace_id = 1` on every existing log row, rate limit and alert rule.

An installation with one workspace looks the same as today. The workspace switcher
only appears when there is a second one.

## Access with workspaces

### The current workspace

- **The dashboard** keeps it in the session, chosen in a switcher next to the
  project picker. The default is the last one used, else the first membership.
- **A personal access token** carries its own (`personal_access_tokens.workspace_id`).
- **Basic auth** (`AUTH_USERNAME`) is the platform administrator. Requests made with
  it name a workspace with the `X-MATE-Workspace` header, else default to 1. This
  keeps scripts written for 1.x working.
- **Widget keys, trigger keys and Slack** reach a project, and so a workspace,
  through the row they authenticate with. No member is involved.

A request for a workspace the caller isn't a member of gets 404, never 403, so the
response doesn't confirm that the workspace exists.

### Three levels of administrator

| Level | Who | May |
|---|---|---|
| Platform administrator | `users.roles` contains `admin`, or `AUTH_USERNAME` | everything, in every workspace; installation settings |
| Workspace owner | `workspace_members.role = owner` | everything in the workspace, including members, provider keys and deleting it |
| Workspace admin | `role = admin` | the dashboard of the workspace as an admin uses it today, without members and provider keys |
| Member | `role = member` | the Work Room, their own tokens and usage, the agents their `agent_roles` allow |

`DashboardAuthzMiddleware` keeps its shape. "Is admin" becomes "is owner or admin of
the current workspace, or a platform administrator". The allowlist for members stays
as it is.

### What stays platform-only

Some settings cover the whole installation, and some features run code with the
server's rights:

- migrations, server control (start and stop the agent server), system settings
  without a workspace, model prices;
- the agent wizard, its partners and shop orders;
- **code executor tools, MCP servers started as local commands (`stdio`), and
  hardcoded agents**: in a workspace these are refused unless a platform
  administrator has enabled them for that workspace.

### The agent runtime

- **RBAC callback:** finds the agent by name (names are unique, D1), takes its
  workspace from the project, and checks:
  1. the caller is a member of that workspace, or a widget, trigger, Slack or
     platform caller for it;
  2. `allowed_for_roles` against the member's `agent_roles`.
- **Fail closed.** 1.x lets a model call through when there is no user id, no agent
  name or no agent config. In 2.0 these refuse, except for the system callers that
  set their own id (`eval_runner`, `trigger_runner`, `widget_*`, `slack_*`).
- **Proxy:** a non-admin may only run agents of workspaces they belong to, on their
  own user id. `/list-apps` returns only those agents.
- **Work Room:** lists only the root agents of the current workspace that the member
  may use. 1.x lists every root agent to everyone and refuses at the model call.

### Provider keys at call time

`create_model()` takes the workspace. With a workspace key for the model's provider,
the key (and base URL) are passed to LiteLLM per call. Otherwise, if the workspace
may fall back, the environment is used. Otherwise the call fails with "This
workspace has no key for <provider>". This covers:

- the agent's model and fallback model;
- the image tool, embeddings for memory blocks, the eval judge and the improver;
- title generation in the Work Room.

`${VAR}` in `model_api_key`, `model_base_url` and MCP configs keeps reading the
environment, so it stays a platform-only form. In a workspace, a non-platform admin
can't save `${VAR}`; they set a workspace key instead.

### Storage namespaces

Sessions, artifacts, memory and credentials stay keyed by app name, and app names
are unique (D1), so no path changes. Nothing has to move on disk or in buckets.

## Dashboard

- **Workspace switcher** in the top bar, hidden with one workspace.
- **Workspace settings** (owner):
  - name and slug;
  - members, with role and agent roles;
  - invitations by email link, or by a copied link when SMTP isn't set;
  - provider keys, shown as `••••1234` once saved;
  - workspace budgets and alerts;
  - leave and delete. A workspace can only be deleted when it has no projects.
- **Platform → Workspaces** (platform administrator):
  - list, create and suspend workspaces;
  - set "may use platform keys";
  - the code-running features per workspace;
  - see any workspace as if a member.
- **Usage, Audit Logs, Guardrail Logs, Traces and Sessions** show the current
  workspace. Platform administrators get an "All workspaces" option.
- **Every place that names an agent** keeps working as today, since names don't
  change.

## Migration and compatibility

- **One migration per dialect** (V044 or later) for the tables and the backfill.
  - It runs in batches of 10,000 rows on the log tables, so a large
    `token_usage_logs` doesn't hold a long lock.
  - On PostgreSQL the new columns are added nullable, backfilled, then constrained.
- **A pre-check before the migration:**
  - stops on duplicate `channel_integrations.team_id`;
  - reports agents whose `parent_agents` name an agent in another project. That is
    allowed in 1.x and becomes refused across workspaces, but within workspace 1
    it stays valid.
- **APIs:**
  - Every 1.x route keeps its path.
  - Responses gain `workspace_id` where they list projects, agents or logs.
  - Routes that act on the installation are unchanged for platform administrators.
- **Rolling back** 2.0 means restoring the database backup. The release notes say so;
  the migration doesn't try to be reversible.

## Testing

- **The cross-workspace test**, the gate for every 2.x release.
  - Fixture: two workspaces, each with a project, a root agent and a sub-agent,
    a widget key, a trigger, a Slack integration, usage and guardrail rows, audit
    entries, a budget, an alert rule, a memory block, a file store, an eval case and
    a member.
  - It lists every route from the app's router, so a new route is covered without
    editing the test.
  - Every `GET` route under `/dashboard/api/` is called as workspace A's admin. The
    test asserts that no id, name or label from workspace B appears in the response.
  - Every write route is called with an id from workspace B, and the test asserts a
    404.
  - It also covers `/apps`, `/run_sse`, the OpenAI API, `/agents/*/mcp`, `/a2a/*`
    and the widget admin API, called with A's credentials on B's agents.
- **The RBAC callback:**
  - A member of A can't run an agent of B, whatever their roles.
  - A model call with no user id, agent name or config is refused.
- **The migration on all three databases:**
  - the backfill puts every row in the default workspace;
  - a one-workspace installation behaves as before, as checked by the existing
    test suite run after migrating;
  - the batches work on a log table with a million rows.
- **Provider keys:** a call in a workspace without a key and without fallback fails
  with the message, and never reads the environment.

## Open questions

1. **Model prices per workspace,** for negotiated rates. They stay
   installation-wide until someone needs this.
2. **Moving a project to another workspace.** Proposed: a platform-admin action.
   The project's logs keep the old `workspace_id`, which is the point of copying it.
   Agents keep their names.
3. **SSO per workspace.** Map an email domain to a workspace, so new users land in
   it with a default role. This needs `OAUTH_ALLOWED_DOMAINS` per workspace and a
   decision on who may set it.
4. **Per-workspace data retention** (`AUDIT_RETENTION_DAYS`). It stays
   installation-wide in 2.0.
5. **Quotas** on agents, projects or storage per workspace, for hosting. Budgets in
   dollars may be enough.
