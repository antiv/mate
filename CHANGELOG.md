# Changelog

All notable changes to MATE (Multi-Agent Tree Engine) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Route inventory test** (#276, 1.6.0 item 1) - `shared/test/test_route_inventory.py` lists every `/dashboard/api/` route of the real app from its router, those of included routers too (FastAPI keeps those out of `app.routes`), and requests each as a non-admin. It fails when a route answers anything but the middleware's 403, or when the allowlists in `server/dashboard_authz.py` let through a route the test does not list as reviewed. The four routes a non-admin may call today (reading and sending ratings, reading their own tokens, renaming a conversation) are the reviewed ones. The test is the skeleton of the 2.0 cross-workspace test. See `docs/dev/auth-and-access.md`

- **Secrets encrypted at rest** (#276, 1.6.0 item 4) - with the new `MATE_SECRET_KEY` (a Fernet key, separate from `SECRET_KEY`), the secrets MATE stores and reads back are encrypted in the database: a Slack integration's bot token and signing secret, a webhook trigger's signing secret, and an agent's literal **Model API Key**, in its version history too. The columns use a new `EncryptedText` type (`shared/utils/secret_box.py`) that encrypts on write and decrypts on read, so nothing else changes. When the key is set on an existing installation, the auth server encrypts the values still stored in the clear at its next start. Without the key everything works as before, and the auth server warns at startup with the number of secrets stored in the clear; 2.0 will require it in production. A key that is not a Fernet key stops the auth server at startup. Keep the key: a secret stored with it reads as empty without it, and has to be entered again. Migration V045 makes the four columns `TEXT`, since an encrypted value no longer fits `VARCHAR(255)`. `cryptography` is now a direct dependency. Checked on SQLite, PostgreSQL 16 and MySQL 8.0 by upgrading a database written by 1.6.0's previous step. See `docs/dev/deployment.md`

### Changed

- **One request context for dashboard routes** (#276, 1.6.0 item 2) - a dashboard route now learns who is calling from one dependency, `get_request_context` (`server/request_context.py`), which gives it the user and their role, instead of resolving the user and calling `_get_is_admin` itself. Every dashboard route uses it: the 24 pages, the API routes of `dashboard_server.py`, and the docs, widget-key and chat-integration routes, which take `get_api_request_context` so that a signed-out caller still gets 401 there rather than a redirect. `_get_is_admin` and `_get_auth_user_dependency` are gone, and 2.0 adds the workspace to the context. Nothing changes for users: a signed-out caller is still sent to the login page, and a non-admin to the Work Room. The route inventory test now also fails on a dashboard route that does not take the context. See `docs/dev/auth-and-access.md`

- **Log rows carry their project** (#276, 1.6.0 item 3) - `token_usage_logs`, `guardrail_logs`, `agent_responses` and `audit_logs` get a `project_id` (migration V044), written with the row: the agent's project, or for an audit entry the project of the agent or project it names. The Usage page's cost by project reads it, so a call stays with the project it was made in when its agent is moved, renamed or deleted; before, every call was counted under the agent's current project, and under **No project** once the agent was deleted. Rows from before the upgrade are filled in from each agent's current project by a backfill the auth server runs once in the background, 10,000 rows at a time, so a large table is never locked for long. Checked on SQLite, PostgreSQL 16 and MySQL 8.0, and on a copy of a real database, where cost by project came out the same as before. See `docs/dev/database-and-migrations.md`

- **`create_model()` takes the caller's context** (#276, 1.6.0 item 5) - `create_model()`, and `create_chat_model()` on the LangGraph runtime, take a `ModelContext`: the agent and project a model is built for. An agent's model and its fallback model get the agent, a subagent gets the agent that started it, and the fallback agent and the compaction summarizer get the installation's empty context. Provider keys still come from the environment, so nothing changes for users; the context appears in the "Creating ... model" log line, and 2.0 looks up a workspace's provider keys from it. `shared/test/test_model_context.py` fails on a call to either function that does not pass a context. See `docs/dev/agent-runtime.md`

### Fixed

- **LangGraph checked roles only for the agent requested, not the one that answered** (#276, 1.6.0 item 7) - on `AGENT_FRAMEWORK=langgraph`, a conversation transferred to a sub-agent goes on there in later turns, but each turn checked the user's roles only for the root agent requested. A user who lost the sub-agent's role, or whose sub-agent's allowed roles changed after the transfer, kept talking to it. Each turn now also checks the agent it resumes in and refuses it as ADK does; the user starts a new conversation to continue. A transfer with no run context, and so no user to check, was let through and is now refused, except in the graph built for LangGraph Studio. See `docs/dev/langgraph-runtime.md`

- **A subagent could send the provider's key to the parent agent's endpoint** - a subagent started by `subagent_delegation` on its parent's model took the parent's `model_base_url` and `model_api_key` as stored, without `resolve_agent_endpoint()`, which builds the parent itself. With a base URL and no key, LiteLLM then sent the provider's key from the server environment to the host the base URL names, and a `${VAR}` key was sent as the literal text `${VAR}`. The subagent now reads the endpoint as the parent does: the variable is resolved, an unset one fails the subtask, and an endpoint without a key gets a placeholder. A subagent on another model given as `default_model` also took the parent's endpoint and key; now only a subagent on the parent's own model does. See `docs/user/subagent-delegation.md`

- **Only admins could create a personal access token** (#276) - the API and the MCP servers accept a token of any user with a role in `ALLOWED_API_ROLES` (default `admin`, `developer`), and the account menu showed **Personal Tokens** to every signed-in user, but the dashboard's access check refused creating and revoking a token to everyone but admins, and the menu said only "Failed to generate token". A user with such a role now creates and revokes their own tokens. The menu shows **Personal Tokens** only to admins and those users, and creating a token refuses anyone else with 403, by the same rule the API applies when the token is used. An `expires_in_days` that is not a positive number now gets 400 instead of 500. See `docs/dev/auth-and-access.md`

## [1.5.1] - 2026-10-17

Every deployment should upgrade, and soon: a signed-in user who is not an admin
could chat as another user, the admin included, by sending a second spelling of the
user id (`userId`) that the proxy did not check. The release also makes the role
check refuse when it cannot decide instead of letting the call through, stops
agents named like a system agent from skipping it, ties file search stores and
budgets to the agent's own project, and deletes a project's integrations, budgets
and test cases with it.

Before upgrading:
- A request to `/run` or `/run_sse` whose body gives `user_id` and `userId` (or
  `app_name` and `appName`) with different values is refused. MATE's own clients
  send one spelling.
- With `MATE_PLUGINS_ENABLED=true`, an agent built in code needs an `agents_config`
  row, or the role check refuses it.

No migrations.

### Fixed

- **Deleting a project left its integrations, budgets and test cases behind** - `_delete_project` removed the project's agents, stores, memory blocks, widget keys, triggers and alert rules, but not:
  - its Slack and other channel integrations, and integrations of other projects pointing at its agents, which stayed active for an agent that no longer existed;
  - the rate-limit configs of the project and its agents;
  - its agents' eval test cases and their results.

  A new agent with a deleted one's name inherited its budget and its test cases. They are deleted with the project now; deleting a single agent still leaves them (#276)
- **The Edit Memory Block dialog had controls that did nothing** (#243) - its minimize and restore buttons and its drag handle are gone, and so are six debug `console.log` calls in the agent form scripts. Thanks to @sourabh65mehta

### Security

- **A signed-in user could chat as someone else, the admin included** - the proxy lets a user who is not an admin run an agent only as themselves, and checked the `user_id` in the body of `/run` and `/run_sse`. ADK also accepts `userId` and uses it when a body has both, so a body with the user's own `user_id` and `"userId": "admin"` passed the check and ran as the admin: past the agents' role checks, and into the admin's conversations, memory and profile. Both spellings are checked now, and a body where they differ is refused. The rate-limit middleware had the same blind spot with `appName`, which let a request be checked against one agent's budget while another agent ran; it now reads the same values ADK uses and refuses a body whose spellings disagree. See `docs/dev/auth-and-access.md` (#276)
- **The role check let a model call through when it couldn't decide** - before each model call the RBAC callback compares the caller's roles with the agent's `allowed_for_roles`, but it allowed the call, with a warning, when there was no user id in the context, no agent name, or no enabled config for the agent, which also happens when the config lookup fails on a database error. It now refuses in all three cases, as it already did when the check raised, and so does the callback chain if the check itself fails to run; the LangGraph runtime's hook refuses an agent without a config the same way. Eval and Playground runs and the system agents still skip the check. With `MATE_PLUGINS_ENABLED=true` the check runs for every LLM agent in the app, so an agent built in code needs an `agents_config` row now. See `docs/dev/auth-and-access.md` (#276)
- **Agents named like a system agent skipped the role check** - the RBAC callback skipped any agent named `mate_fallback_agent`, `system_agent` or `health_check_agent`, and the dashboard and the `create_agent` tool accept any name, so an agent given one of them answered everyone, whatever its `allowed_for_roles`. Only the fallback agent MATE builds itself when the configuration cannot load skips the check now, recognised by its class rather than by name; the other two names belonged to no agent (#276)
- **A chat request could pick the project its budget was checked against** - the rate-limit middleware read `project_id` (or `projectId`) from the body of `/run_sse` and checked the request against that project's limits instead of the agent's, so a caller could name a project without a budget and escape the one on the agent's project. It now ignores the body's project and the rate limit service looks the project up from the agent, as the OpenAI-compatible API already did (#276)
- **A file search store could be assigned to an agent of another project** - `assign_store_to_agent`, behind `POST /dashboard/api/agents/{name}/file-search/stores/assign`, checked that the agent and the store existed, not that they belonged to the same project, so an agent could be given another project's documents. It now refuses a store of another project. The dashboard already offered only the project's own stores; **Clone to Project** with file search still shares the source's stores on purpose (#276)

## [1.5.0] - 2026-10-17

Every deployment should upgrade: the agent, Google Drive and image MCP servers
answered without a login, an empty `AUTH_PASSWORD` let anyone sign in with empty
fields, any signed-in user could fire a trigger, and a widget admin key could change
another agent's files. The release brings **costs in US dollars** with dollar budgets,
a **Playground** to compare models and instructions, **Trace Replay** on the Visual
Builder canvas, an image tool for **any LiteLLM image model**, alerts on **fallback
model** use and to **Slack and Discord**, and a **Docker image on GHCR**. MySQL works
for the first time, and A2A works again on a2a-sdk 1.x.

Before upgrading:
- MCP clients of `/agents/<name>/mcp`, `/gdrive/mcp` and `/images/mcp` need
  credentials, for example a personal access token.
- `POST /triggers/{id}/fire` without the trigger's key needs an admin.
- With `CSP_MODE=enforce`, inline scripts need the nonce; `report-only`, the default,
  blocks nothing.
- `SUPABASE_BUCKET` defaults to `artifacts` everywhere; set it if you relied on
  `public-bucket`.

Migrations V037 to V043 run on startup.

### Added

- **Trace Replay on the Visual Builder canvas** - a traced chat session can be played back on the agent graph as one run, turn after turn (the time between turns cut to a short pause): agents light up in the order they ran, the edge into a sub-agent lights up while it is handed the conversation, each agent shows the model or tool it is using and how long it has run, finished agents show their time, and an agent whose model failed shows which fallback model answered. The session list only offers sessions of the project on the canvas. Open it with **Replay** in the Visual Builder toolbar, or **Replay on canvas** on a trace. It reads the `trace_spans` rows recorded with `OTEL_TRACING_ENABLED=true` (ADK runtime only), through two new admin routes, `GET /dashboard/api/traces/sessions` and `GET /dashboard/api/traces/replay`. The `gen_ai.inference` span now records `mate.model.fallback` and the fallback as `gen_ai.response.model` when the fallback answered. See `docs/user/trace-replay.md`
- **Docker image on GitHub Container Registry** (#249) - every release tag now publishes `ghcr.io/antiv/mate` for `linux/amd64` and `linux/arm64`, tagged `1.5.0`, `1.5` and `latest`, so MATE runs with one `docker run` and no clone or local build. The workflow starts the image and checks `/health` before it pushes anything. See `docs/dev/deployment.md`
- **Agent MCP servers offer a prompt and a resource** - next to its tool, each agent exposed over MCP now lists the prompt `ask_<agent>`, which asks the agent a question through its tool, and the resource `mate://agents/<agent>/about`, with the agent's name, description and how to call it (not its instruction). `initialize` announces both capabilities. See `docs/dev/mcp-servers.md`
- **`GET /mcp` lists the agents exposed over MCP** (#231) - with a personal access token (or any other way the agent servers accept), it returns the agents in `MCP_EXPOSED_AGENTS` that the caller may use, each with its name, description and full MCP URL, so a client can be set up without knowing the names. See `docs/dev/mcp-servers.md`
- **Agent MCP servers describe themselves** (#231) - the `initialize` response of `/agents/<name>/mcp` now carries the agent's description in `serverInfo` (`title`, `description`) and as `instructions`. MCP registries read it: without it the LobeHub market CLI (`lhm plugin init`) refused the server with "Could not infer a plugin description"
- **Playground: compare models and instructions side by side** (#178) - on the Evals page, a **Playground** tab runs one prompt, or an agent's active eval suite, on two or three variants of the agent: its current config or a stored version, with another model and/or other instructions. It shows each variant's average score, reply time, cost in US dollars and tokens, and every reply with its own score, time and cost. Variants run in memory like eval runs: nothing is saved or deployed, and a variant that cannot run shows its error while the others still run
- **Cost in US dollars, not only tokens** (#178) - tokens were tracked per agent, user and project, but no price was shown anywhere. Every model call is now priced when it is logged (`token_usage_logs.cost_usd`), and the Usage page shows the period's cost, the cost per agent and per project, and what agents' fallback models cost apart (calls a fallback answered are now marked, `is_fallback`). Prices come from a price set on the new **Model prices** part of the Settings page, then for `openrouter/` models OpenRouter's own price list (LiteLLM has no entry for most of them, and reports 0 for those), then LiteLLM's. A model none of them prices has no cost rather than a cost of $0, and the Usage page shows the share of tokens left out. Setting a price reprices the model's past calls; **Fill in missing costs** prices calls logged before their model had a price, and `openrouter/` calls logged before OpenRouter's list arrived are priced as soon as it does. Costs are list prices, without discounts for cached input. Rate Limits takes budgets in dollars per day and per month for a user, agent or project (`usd_per_day`, `usd_per_month`), each measured against that scope's own spend, with the same warn or block actions, and the **Budget threshold** alert can be in dollars, with its own limit or the Rate Limits one. Calls without a price do not count toward a dollar budget
- **The image tool takes any image model LiteLLM supports** - the agent form offered three models, and the tool had two hand-written paths: OpenAI's `images.generate` and a separate Nano Banana path through OpenRouter chat, so Imagen, FLUX, Stability, Recraft, xAI or Bedrock models could not be used. Generation now goes through `litellm.aimage_generation`, except for OpenRouter models, which go through its chat completions asking for an image (`modalities`), since LiteLLM's image call for OpenRouter leaves that out and the model then answers in text only. Models are named as for agents (`gemini/gemini-2.5-flash-image`, `black_forest_labs/flux-pro-1.1`, ...) and each provider's usual key; Gemini also takes `GOOGLE_API_KEY`. **Image Model** in the agent form is a text field with suggestions. The default for agents that name none is set on a new **Settings** page in the dashboard (Control Room → System), or with the new `IMAGE_MODEL`, else `dall-e-3`; the page shows which applies and whether its provider's key is set, and every change is audited. The page stores its values in a new `system_settings` table (migration V040). Existing configs keep working: bare OpenAI names with the same defaults and key fallbacks, `nano-banana`, and the tool name `generate_image_nano_banana`. Images are saved with the type they came in (JPEG, WebP, ...) instead of always as PNG; only PNGs carry the Art. 50 AI-generated marker. The setup check behind `/images/mcp/health` now looks for the default model's key instead of listing OpenAI models. See `docs/dev/image-generation.md`
- **Alert when agents answer on their fallback model** - the fallback model hides a provider outage from the person chatting, and the *Agent errors* condition could not show it either: on ADK it counts the primary's failure like any other error, on LangGraph not at all. A new **Fallback model used** condition counts the requests a fallback answered in a time window, per agent, project, user or globally, from the `agent.model_fallback` audit entries both runtimes write. Its webhook event is `model_fallback_alert`. See `docs/user/alerts.md`
- **Slack and Discord as alert destinations** - an alert posted to a Slack or Discord webhook URL as an HTTP destination was refused, since those take a fixed body (`text`, `content`) rather than MATE's payload. Alert rules can now name a **Slack (incoming webhook)** or **Discord (webhook)** destination, which posts the alert message as text. Slack's `<` `>` `&` are escaped and Discord's mentions turned off, so a rule or agent name cannot ping the channel, and the rule list shows the destination type instead of the webhook URL. See `docs/user/alerts.md`
- **Ratings from standalone builds** - a standalone build showed no 👍/👎, since it had nowhere to keep them, so whoever distributed one never learned which answers were bad. With `MATE_FEEDBACK_URL` and `MATE_FEEDBACK_KEY` (the standalone feedback key of a widget key for the same agent, shown under **Embed Code**) in the build's `.env`, the chat shows the buttons and the note after a thumbs-down, and the build forwards each rating to that MATE, where it lands in **Evals → Rated Down** with **Add to evals** and **Suggest a fix**. The central server cannot read the build's sessions, so the build sends the rated question and answer, read from its own session store, never from the browser; they are stored on the rating (migration V038) and shown as *sent by a standalone build*, and used only when the server has no session for it. The server takes them only with the feedback key (`X-Widget-Feedback-Key`, migration V039 generates one for every widget key): with the public key, which is in every page that embeds the widget, the rating is recorded and the text ignored, so a visitor cannot feed made-up exchanges to Suggest a fix. A rating can no longer be changed through another agent's widget key. The build's `/feedback` route takes at most 30 ratings a minute per client, and forgets idle clients. Off by default. See `docs/dev/standalone-build.md` (#125)

### Changed

- **Patch releases for an older minor version** - a release tag now marks the GitHub release as *Latest* and the Docker image as `latest` only when it is the highest final `vX.Y.Z` tag, so a `v1.5.1` cut from a `release/1.5` branch after `v2.0.0` leaves both on 2.0.0. CI also runs on pushes to `release/**`. How releases and maintenance branches work: `docs/dev/releases.md`
- **Dollar budgets can see calls without a price and sub-agents** (#222) - a dollar budget could quietly never trigger: calls on a model with no price (`openrouter/` models while OpenRouter's price list can't be fetched, local or new models) added nothing to the spend, a database error while summing counted as $0 and let the request through, and an agent's budget left out what its sub-agents spent. Each Rate Limits config now has **Treat calls without a price as over the dollar budget** (`block_unpriced`) and, for agents, **Include sub-agents** (`include_sub_agents`, counting the agent and every agent below it in its daily token limit and dollar budgets); both are off by default (migration V043). A **Block** dollar budget now refuses the request when its spend cannot be read. The Usage vs Limits panel warns when the scope had calls without a price, and a dollar **Budget threshold** alert says how many calls it left out
- **google-adk 2.9.2 → 2.11.0** (#147) - ADK 2.10 and 2.11 list no breaking changes; among their fixes, `/run_sse` now cancels a run when its client disconnects, and a new index on the session events table is added on startup. Only ADK moves: the rest of the lock waits for the monthly upgrade, which takes releases at least a week old. Checked with the unit tests and a real conversation through the auth server on both runtimes, on SQLite and on PostgreSQL 16, which also ran migrations V037 to V040 there for the first time
- **Monthly dependency upgrade** (#147) - a scheduled workflow recompiles `requirements.lock` on the 1st of each month with the newest allowed releases at least a week old (a package the lock already has at a newer release stays there rather than moving back), runs the tests on it in a job with a read-only token and no stored credentials, and opens a pull request listing what moved from a second job that installs nothing; actions are pinned by commit; Dependabot keeps the GitHub Actions current. See the README's Dependencies section
- **Migration V037** drops the CHECK constraints V026 put on `alert_rules.condition_type` and `destination_type` (SQLite rebuilds the table, keeping every rule), which refused any type added after them. The dashboard API validates both lists, and tables created by SQLAlchemy never had the constraints
- **One default per environment variable** (#157) - `SUPABASE_BUCKET` defaults to `artifacts` everywhere. The Supabase storage tools used `public-bucket`, so with the variable unset they and the artifact service wrote to different buckets; if you relied on that, set `SUPABASE_BUCKET`. Image links for Supabase artifacts are now built with that default too, where they used to be left out. Because the tools and the artifacts now share a bucket by default, the Supabase storage tools refuse paths under `public/`, where the artifact service keeps every user's chat artifacts; an agent steered by a chat message could otherwise read, overwrite or delete them. This applied already to deployments that set one bucket for both, as the old `.env.example` did. `DB_NAME` defaults to `mate_agent` for the application database as it already did for sessions, so PostgreSQL and MySQL need only `DB_USER` and `DB_PASSWORD`. Defaults now live in `shared/utils/settings.py`
- `get_otlp_endpoint()` and `get_otlp_protocol()` are removed from the tracing config; nothing called them, and the `http://localhost:4318` default they advertised never applied: without `OTEL_EXPORTER_OTLP_ENDPOINT` no OTLP exporter is added

### Fixed

- **Traces showed no span attributes on PostgreSQL** - `trace_spans.attributes` is `JSONB` there, which the driver returns already parsed, and the Traces page only parsed text, so every span showed none. It now takes both
- **A2A was off on the pinned a2a-sdk 1.2.1** (#223) - `adk_main.py` was written for a2a-sdk 0.3, whose `a2a.server.apps` no longer exists in 1.x. Starting with `--a2a`, which the dashboard always does, printed "A2A dependencies not available" and mounted no `/a2a/*` routes, so A2A calls failed, and so did agents exposed over MCP, which go through A2A.
  - **The fix:** the routes are now built through ADK's `google.adk.a2a._compat` (`shared/utils/a2a_routes.py`), which works on 0.3 and 1.x.
  - **Method names:** `/a2a/{agent}` accepts the 1.x methods (`SendMessage`, `GetTask`, ...) and the 0.3 ones (`message/send`, `tasks/get`, ...).
  - **The card:** it is served in the 1.x form (`supportedInterfaces`). It now names the agent's own endpoint (`/a2a/{agent}`) instead of the server root. Its input and output modes are `text/plain`, since the agent reads and writes text. An `agents/<name>/agent.json` in the 0.3 form is still read and converted.
  - **MCP replies:** the MCP bridge returns the agent's reply text, taken from the task's artifacts, instead of the whole task printed as a dictionary.
  - **A test** now fails when a dependency bump breaks the routes; until now the only sign was a line at startup.
- **MATE did not start on a new MySQL database** (#178) - the schema was never created on a new MySQL database, so the server ran without tables.
  - **The migrations stopped at V001.** MySQL takes a default on a `TEXT` column only as an expression (`DEFAULT ('x')`), and the `credentials` unique key was longer than MySQL's 3072 bytes. Twenty later migrations then failed on the missing tables or on `ADD COLUMN IF NOT EXISTS`, which MySQL does not have.
  - **The fix edits the released MySQL files**, which the guide otherwise rules out. Those statements never ran on any MySQL, and nothing re-runs a version that is already recorded.
  - **The credentials key now takes the first 255 characters** of `credential_key`.
  - **Re-runs skip indexes that exist.** On MySQL, a re-run now skips "Duplicate key name" errors, as it already skipped duplicate columns.
  - **The agent server kept no sessions on MySQL.** It did not recognise the `mysql+pymysql://` session URL and fell back to an in-memory store, which also failed to start, so every new chat failed. Sessions are now stored in MySQL through `aiomysql`.
  - **Checked** on a fresh MySQL 8.0: all 42 migrations at startup, every dashboard page, prices, dollar budgets and alert rules, and a conversation, which was stored and priced.
- **`DB_TYPE=mysql` failed at startup on a default install** - MATE connects to MySQL with `pymysql`, and ADK keeps sessions on MySQL with `aiomysql`, but both were commented out in `requirements.txt` and missing from `requirements.lock`, so the server stopped with `No module named 'pymysql'`. Both are now installed, like the PostgreSQL drivers (#178)
- **The Visual Builder's History button did nothing** - the agent form on the Visual Builder has a History button, but the page loaded neither the version history panel nor its script, so clicking it raised an error. It now opens the agent's version history, as on the Agents page
- **`shared/migrate.py` without a `.env` looked for PostgreSQL** (#157) - run on its own, the migration runner fell back to `DB_TYPE=postgresql` and, for SQLite, to `mate_agent.db` relative to the working directory, while the application uses SQLite and `my_agent_data.db` in the project root. `migrate.py status` and `run` therefore failed with "No database engine available", or migrated a different file. The runner, the database client, the session store and the dashboard now read the database settings from one place
- **MySQL sessions used port 5432** (#157) - with `DB_TYPE=mysql` and `DB_PORT` unset, the application database used 3306 but the ADK session store 5432
- **Alerts from a global rule read "global None"** - every message from a rule with the global scope began "global None …", in emails, webhooks and, since the Slack and Discord destinations, in chat. It now says "all agents". The `message` text of existing HTTP and email alerts changes accordingly; `scope` and `scope_id` in the payload do not. The fallback alert's message now reads "The fallback model answered for … N times …"
- **The widget showed agent server errors cut off** (#229) - the widget chat route copied every header of the agent server's `/run_sse` reply, `Content-Length` included, and put its session event in front of the body, so an error reply ran past its length and was cut off, with an SSE line in front of the JSON. It no longer copies `Content-Length`, `Content-Encoding`, `Transfer-Encoding`, `Connection` or `Keep-Alive`, and adds the session event only to a successful reply, so the widget can read an error's detail
- **The Documentation page's API tab pointed at port 8000** (#240) - Swagger and ReDoc were loaded from the dashboard's host on a hard-coded port 8000, so with the dashboard published on another port (Docker `-p 9000:8000`) the tab showed nothing. They now load from the auth server's own `/docs` and `/redoc`, which already forward to the agent server. Thanks to @ningyixi
- **Help panel links to `https://dashboard/docs`** (#234) - the help agent often rewrote the `/dashboard/docs?...` links its tool gave it as `https://dashboard/docs?...`, which the panel treated as an external site and opened on a host that doesn't exist. Such links (and `//dashboard/...`, `dashboard/...`) are now turned back into `/dashboard/docs` before the answer is shown. Thanks to @ningyixi
- **An empty Work Room offered non-admins a link to create an agent** (#239) - the link led to the Agents page, which sends non-admins back to the Work Room. It is now shown to admins only; others are told to ask an admin. Thanks to @ningyixi
- **Notifications ignored their duration** (#242) - `showNotification` always closed after 5 seconds, so the Triggers page's test-fire result, meant to stay 8 seconds, went as fast as the rest. It now takes the duration, 5 seconds by default. Thanks to @kocaemre
- **The Visual Builder's Add MCP Server and new agent dialogs were always dark** (#245) - they used fixed dark colors and now use the dashboard's theme colors, so they follow the light theme. Thanks to @CRYPTONIKAV
- **Icon-only buttons on every page had no name for screen readers** (#246) - the agent server's Start, Stop and Restart, the sidebar toggle, and the close buttons of the tokens dialog and of notifications now have an `aria-label`. Thanks to @CRYPTONIKAV
- **The Integrations page showed the Control Room sidebar** (#237) - `/dashboard/integrations` was missing from the Studio pages in `base.html`, so it opened with the Control Room menu and its own item was nowhere to be seen. Thanks to @sammist2
- **Rate Limits had an Alert webhook URL field that did nothing** (#241) - budget alerts moved to the Alerts page in V026, which copied the old URLs into alert rules, but the form still offered the field. It is gone; the column stays, and saving a config through the dashboard now clears it. Thanks to @cestercian

### Security

- **A widget admin key could change another agent's files** - the widget admin file routes took any `store_name` for an upload and deleted any file search document by id, so the admin key of one widget could add files to, or delete files from, any agent's stores, in any project. `POST /widget/api/files/upload` now refuses a store not linked to the key's agent (`404`), and `DELETE /widget/api/files/{id}` answers "File not found" for a file in such a store
- **Any signed-in user could fire a trigger** - `POST /triggers/{id}/fire` without the trigger's key fell back to dashboard login, which accepts any user, not only admins, and the route sits outside `/dashboard/api`, where admin-only access is enforced. Without the key, firing a trigger now needs an admin (`403` otherwise); callers with the fire key are unchanged. See `docs/dev/trigger-engine.md`
- **"My Usage" showed every user's usage** - the usage page is open to people without the admin role, as **My Usage**, but it rendered the figures of the whole installation: totals, the busiest agents, cost by agent and project, ratings and latency, and in the **Raw Data** view the last day's model calls of every user, with their user IDs. The page now narrows every figure to the signed-in user's own traffic unless they are an admin; ratings are those given in their own conversations. The APIs behind the page were already admin-only
- **Confirmation dialogs showed names as HTML** - `showConfirm` put its title, message and button labels into the page as markup, and some messages carry a name someone else chose (an imported template, a rate-limit scope such as a widget visitor's user ID). They are set as text now
- **The agent and Google Drive MCP servers needed no login** (#231) - every route of `/agents/<name>/mcp` and `/gdrive/mcp` but the health check answered without credentials. Anyone who reached the server could run the agents in `MCP_EXPOSED_AGENTS` on its provider keys, outside per-user budgets and the agent's `allowed_for_roles` (the call reaches the agent over A2A without the caller's identity), and could list, search and read Google Drive files, sharing permissions included, through the server's service account. Both now take the callers the OpenAI-compatible API takes: a personal access token (`Authorization: Bearer mate_pat_...`), Basic auth, the login token or a dashboard session, of an admin or a user with a role in `ALLOWED_API_ROLES`; an agent's routes also require a role the agent allows, and an agent with none is admin-only. Without credentials they answer 401, without access 403. **MCP clients that connected without credentials, including other MATE instances that use these agents through `mcp_servers_config`, now need a token**; `${VAR}` in `headers` keeps it out of the config. See `docs/dev/mcp-servers.md`
- **An empty `AUTH_PASSWORD` let anyone log in with empty fields** - `.env.example` lists `AUTH_USERNAME` and `AUTH_PASSWORD` empty, and `os.getenv` returns `""` for them rather than its default. So the Quick Start's `cp .env.example .env` configured an empty username and password, and both the login form and Basic auth (`curl -u ":"`) were accepted, admin APIs included. The production check looked only for the default password `mate`, so it let an empty one through, also with `MATE_ENV=production`. Empty values now count as unset: the built-in account is `admin` / `mate`, which development warns about and production refuses at startup. An empty password never matches, whatever is configured. The credentials are read in one place, `shared/utils/settings.py`
- **Python in the Work Room canvas runs in the sandbox** - HTML and JavaScript already ran on the sandboxed canvas page, but Python ran in an unsandboxed frame on MATE's origin. An agent writes that code, so instructions planted in what it reads (a web page, a document) could produce Python whose `js` module acted as the person who clicked **Run**: an admin's session could create users or read agent configs, keys included. Python now runs on the sandboxed page too, where it can reach neither the dashboard nor MATE's storage; **Open in new tab** also runs Python there, instead of treating it as JavaScript.
- **CSP allows scripts from jsdelivr and cdnjs only by path** - `script-src` allowed the whole of `cdn.jsdelivr.net` and `cdnjs.cloudflare.com`, which serve any npm package or GitHub repository, so an HTML injection could load the attacker's own script even under `enforce`. The policy now lists each library's path with its version (Chart.js, Monaco, React and React Flow, markdown-it, Swagger UI, Redoc, Ace), and a test fails on a template script it does not cover. `'wasm-unsafe-eval'` is gone from the dashboard's policy, since Pyodide no longer runs there. A custom template that loads another script from those CDNs needs its path in `CSP_EXTRA_SOURCES`
- **OpenRouter prices are checked before use** - a price that is not a finite number from 0 to $0.1 per token is skipped, so a NaN in the list can no longer make every sum over a model's calls NaN and with it the dollar budgets, and the list is read up to 25 MB
- **Playground runs are audited** - each run writes `evals.playground_run` with the admin who started it, the mode, and each variant's model and version; changed instructions are recorded by length and hash, not as text. The runs themselves are logged under the eval user, so this is what ties them to a person
- **CSP no longer allows `unpkg.com` and `d3js.org`** - the only page that loaded from them, `templates/graph.html` (a graph view no route served), is removed. A custom template that loads a script from either host now needs it in `CSP_EXTRA_SOURCES`
- **CSP: `enforce` now blocks inline scripts without the nonce, and inline handlers** (#124) - every response gets a fresh nonce, and the policy allows inline scripts only with it and no inline `onclick=` handlers. Without `'unsafe-inline'`, the policy stops an injected script rather than only limiting where it loads code from. `CSP_MODE=enforce` enforces it; in 1.4 it enforced a policy with `'unsafe-inline'`. `CSP_MODE=report-only` (the default) reports against it and blocks nothing; it is also the way back if a page breaks under `enforce`. ADK's dev UI, whose markup is not MATE's, keeps `'unsafe-inline'`. Handlers become `data-click` actions (`static/js/csp-actions.js`), which call only functions the page allows. Converted: the layout every dashboard page shares, the login page, the overview, the Agents page and the Visual Builder with all their modals, the monitoring pages (alerts, audit logs, guardrail logs, sessions, traces, rate limits, integrations, triggers), evals, usage, users, migrations, docs, the template gallery and the wizard pages (leads, orders, pricing), and the Work Room, the widget chat and admin pages and the standalone chat: every page. The Work Room canvas runs agent code on a page of its own (`/dashboard/workroom/canvas`) whose policy sandboxes it, so that code keeps its inline scripts, `onclick=` and CDNs once the dashboard's policy is strict. A library from a CDN no list names (`cdn.plot.ly`) now loads in the canvas too. The admin Swagger UI's inline script gets the nonce, and the agent wizard (`/wizard/embed`) takes its `frame-ancestors` from the partner's allowed origins: it had the dashboard's `frame-ancestors 'self'`, so under `CSP_MODE=enforce` no partner site could frame it. Doing so also fixed:
  - the token list's Revoke button, which put the token name into a JavaScript string unescaped;
  - the file search store list, which did the same with store names;
  - the wizard leads list, which did the same with the company name a visitor typed into the wizard;
  - the integrations list, which showed labels, agent names and team IDs as HTML and put each row into an inline handler unescaped;
  - the usage log table and its details, which showed user IDs, agent and model names and error text as HTML (a widget visitor partly chooses their user ID);
  - the migrations page's notices, which showed a server error as HTML;
  - the widget admin panel's memory block list, which put block IDs into an inline handler unescaped
- **"Open in new tab" ran canvas code on MATE's origin** - the Work Room canvas opened an agent's HTML or JavaScript in a new tab as a `blob:` URL. A `blob:` page has the origin of the page that made it and no sandbox, so code an agent wrote, steered by a prompt injection for example, could call the dashboard API as whoever clicked. The tab now opens the sandboxed canvas page, whose code has no access to MATE
- **The image tool could be pointed anywhere through `tool_config`** - since the image tool moved to LiteLLM (unreleased), every `tool_config.image_tools` key but a short denylist reached `litellm.aimage_generation`. An agent steered by a chat into `update_agent` could set `mock_response` to an internal URL, which the tool then downloaded and saved as an artifact (cloud metadata, the ADK server), route every prompt to an outside logging host, or point Bedrock at its own endpoint. Only image parameters pass now (`size`, `quality`, `n`, `style`, `aspect_ratio`, ...), and an image URL from a provider is downloaded only over http(s), without redirects, at most 25 MB and not from a private address (`IMAGE_ALLOW_PRIVATE_NETWORK` allows it for a local model)
- **`/images/mcp` needed no login** - its tools spend the server's provider keys, and the Nano Banana tool took the model and request parameters from the caller. Every route but the health check now needs a signed-in caller, callers can no longer choose the model, and the public health check no longer names the default model or missing keys. The image MCP server had also been off since the image tool change, whatever keys were set: its startup check read fields the setup check no longer returns
- **Notifications rendered their message as HTML** - `showNotification` put its text into the page as markup, and some messages echo server or user input (an error naming the model you typed). It is set as text now, for every dashboard page
- Image generation errors given to the agent carry the first line of the provider's message, not LiteLLM's traceback and server paths, which stay in the log

## [1.4.0] - 2026-10-05

Every deployment should upgrade: the built-in admin account was refused by
admin-only agents on a new installation, a disabled webhook trigger still ran,
and two pages could run script stored by a widget visitor or a widget key's
holder. The release also brings a **fallback model** for provider outages, a
**Content-Security-Policy** (Report-Only), the **documentation inside the
dashboard** with a **help agent** behind a ? button, and memory blocks that
honour their Read-only and Character Limit settings.

Migrations V034 to V036 apply on startup. V035 replaces a Tavily API key that
older seeds wrote into agents' MCP URLs with `${TAVILY_API_KEY}`; set that
variable if an agent uses Tavily.

**Before upgrading:**

- Install from `requirements.lock` (`pip install -r requirements.lock`); Docker
  and CI already do. `requirements.txt` is now only the list of direct
  dependencies, and SQLAlchemy is pinned below 2.1.
- With `MATE_ENV=production`, the server exits at startup when it cannot reach
  the database instead of serving an empty dashboard, and ADK's dev UI at
  `/dev-ui` is off unless `ADK_DEV_UI=true`.
- A memory block marked Read-only can no longer be changed or deleted, and a
  value longer than its Character Limit is refused, whether the write comes
  from an agent, the dashboard, the widget admin API, a trigger or a template
  sync. An agent that relied on writing such a block now gets an error; untick
  Read-only or raise the limit if that write is wanted.
- Saving a trigger with an invalid cron expression, an `http_callback` without
  a URL, or an email output without a recipient or `SMTP_HOST` is now a `400`.
  Existing triggers like that keep their config, but their runs are recorded
  as errors instead of `ok`.
- `docker-compose.yml` now loads `.env` into the container, so every setting
  in it reaches MATE, and `DB_TYPE` and `ARTIFACT_SERVICE` come from it
  (defaults `sqlite` and `local_folder`; it used to force `supabase`).
- The Slack integration's **Mention-only** box is gone. It was never read: in
  channels the agent has always answered only when mentioned.
- `python shared/migrate.py rollback` is removed; it failed for every
  migration. Reverse a change with a new migration or restore a backup.

### Added

- **Documentation in the dashboard** - the documentation was a folder of Markdown files nobody saw from inside the product, and it drifted from the code. It now lives in `docs/` and is served at `/dashboard/docs` with search: user guides, developer guides, and a reference (configuration, HTTP API, tools, database) that `scripts/gen_docs.py` generates from the code. CI fails when the reference is stale and warns when code covered by a guide changes without the guide. The feature guides from `documents/` moved into `docs/`; pages not yet re-checked against the code are marked as such. See `docs/dev/documentation.md`
- **Content-Security-Policy** - MATE sent no CSP, so an injected script could load code from any host and send data to one. Every page now gets a policy that limits scripts, styles, fonts and connections to MATE itself and the CDNs its templates use, forbids plugins and `<base>` rewrites, and lets only MATE frame the dashboard. The widget chat page takes its `frame-ancestors` from the widget key's allowed origins when `WIDGET_ORIGIN_STRICT` is on, covering the same sites as the origin check, so once enforced it also stops a site that hides its referrer. It ships as `Content-Security-Policy-Report-Only` (`CSP_MODE`): nothing is blocked, and violations are logged once each through `/csp-report`. `CSP_EXTRA_SOURCES` adds hosts a deployment needs. Inline scripts are still allowed; removing them is the next step. See `docs/dev/content-security-policy.md` (#124)
- **Fallback model** - when an agent's model failed, the person chatting saw an error, even with healthy providers one config field away. An agent can now name a **Fallback Model** (agent modal, visual builder, or `fallback_model` on the agents API). A request whose model call fails because the provider is unavailable (a timeout, a connection failure, a 5xx or a 429) is re-run once on it, and the answer goes back without an error. Errors the request itself causes, such as a content-policy refusal or an oversized context, do not fall back, so a refusal cannot be used to get past the primary provider's filters. Guardrails and token logging apply to it as to any answer, usage logs book its tokens under the fallback model, and each fallback leaves a warning and an `agent.model_fallback` audit entry. The fallback is reached through the provider env vars, never the agent's own base URL and key. Works on both runtimes. Migration V034. See `docs/user/fallback-model.md` (#41)
- **Suggest a fix can change the memory blocks the agent read** - when a wrong answer came from a block, such as outdated opening hours, the fix could only patch the instruction around it. The suggestion now also lists the blocks the agent read in that answer (from the rated turn, or by running the test case once in memory), and the model may revise the values of the ones you tick; read-only blocks are never offered. The check runs the suite with the new values standing in for the stored ones inside the in-memory run, without writing them, and warns which other agents of the project share the blocks. Apply is refused if a block changed meanwhile, and writes each block as a block version under your name, listed in the audit entry. See `docs/user/evals.md` (#136)
- **Evals against a stored version on the LangGraph runtime** - with `AGENT_FRAMEWORK=langgraph`, running an eval against a version, a suite run and "Check against suite" returned `501`. They now build the graph from the version's snapshot, with sub-agents from their current config, and run it against an in-memory checkpointer, as the ADK runtime does. The deployed graph, the LangGraph session tables and the configured artifact storage are untouched, and RBAC is skipped for the eval run on both runtimes. See `docs/user/evals.md` (#123)
- **Help agent** - a **?** button on every dashboard page opens a chat with `mate_help`, a built-in agent that searches and reads MATE's documentation through the new `docs` tool and knows which page you are on. Admins get every section with links to the Documentation page; other users get the user guides only. Migration V036 creates it in its own MATE Help project, open to the admin and user roles, with no model set, so it uses the server's default until an admin picks one; it is only created when no agent of that name exists. See `docs/user/help.md`
- **The database in `/health`** - `/health` now answers `503` with `"database": "unavailable"` when the database cannot be reached, so the Docker `HEALTHCHECK` and orchestrators notice. PostgreSQL and MySQL are retried at startup (`DB_CONNECT_RETRIES`, default 5, 3 s apart) for a database that starts after MATE, and with `MATE_ENV=production` the server exits if it still cannot connect (#144)
- **Hashed lock file** - `requirements.lock`, compiled with uv for Python 3.11 on every platform, pins every package with hashes. Docker, CI and the Quick Start install from it, so rebuilding the same commit can no longer pick up a breaking release. The README says how to regenerate and upgrade it (#143)

### Changed

- **ADK's dev UI is off in production by default** - `/dev-ui` is a debugging tool that shows admins every event, tool argument and session state, runs on the dashboard's origin, and needs `'unsafe-eval'`. With `MATE_ENV=production` it is no longer served unless `ADK_DEV_UI=true`; elsewhere nothing changes. The CSP only allows `eval()` there while it is served. The dashboard's Chat button already opens the Work Room, so nothing in the dashboard depends on it
- **The Slack integration's Mention-only option is removed** - the form saved it but nothing read it: in channels the agent always answered only when mentioned, plus direct messages. The Slack guide says so (#154)
- **`migrate.py rollback` is removed** - it looked for `R<version>` files no migration ships, in the wrong folder, so it failed for every migration. The CLI now says to write a reversing migration or restore a backup (#156)
- The dashboard's old dev UI chat side panel is removed; nothing could open it, as the Chat button opens the Work Room

### Fixed

- **Memory blocks ignored Read-only and Character Limit.** Both were stored in a block's metadata and never checked. Changes, deletes and restores of a read-only block are now refused unless the write clears the flag, as is a value over the limit, with `error_code` `read_only` or `too_long` and a message an agent can act on. The dashboard's edit form now saves the Read-only, Character Limit and Preserve on migration fields it always sent, a trigger whose answer cannot be written to its block fails the run instead of dropping the answer, and template sync skips such blocks and lists them in the sync dialog. Importing agents with overwrite still replaces blocks directly (#153)
- **Triggers reported success when nothing was delivered or scheduled.** An invalid cron expression, an `http_callback` without a URL and an email output without a recipient or `SMTP_HOST` were saved without complaint, and their runs recorded as `ok`. Create and update now answer `400` with the reason (update only checks when it changes the type, schedule or output, so rotating an older trigger's key still works), and such a run is recorded as an error with the agent's answer kept (#151)
- **`docker-compose` did not pass `.env` to the container**, so LLM API keys and every setting not listed under `environment:` never reached MATE. It is loaded with `env_file` now (optional), and the explicit entries still win. `DB_TYPE` and `ARTIFACT_SERVICE` are taken from `.env` instead of being forced to `sqlite` and `supabase`, and `DB_PASSWORD` is no longer required (#159, #163). Thanks to @cestercian
- **The agent server was started with `python` from `PATH`** instead of the interpreter running the auth server, so in a virtualenv it could start without MATE's dependencies. It uses `sys.executable` now (#155). Thanks to @cestercian
- **Migrations failed on a fresh PostgreSQL database and on schemas made by `create_all`.** V002's seed relied on a unique constraint the `MemoryBlock` model lacked, so when the ORM created the table first every insert failed; the model declares it, V002 adds it when missing, and `create_all` only runs after migrations succeed (#145). On tables made by `create_all`, which have no database-side defaults, V001 to V003 and V036 left NOT NULL timestamps and flags to the database and failed (V036 was even recorded as applied on SQLite without creating the agent); they set every such column themselves now, and the V001 and V003 agent seeds only insert missing agents instead of overwriting ones an admin may have edited
- **`migrate.py create` wrote a single dialect.** It now writes the new file into `sqlite/`, `postgresql/` and `mysql/`, numbered after the highest version in any of them, and `gen_docs.py --check` in CI fails while a version is missing from a dialect (#156)
- **The server ran without a database after a rebuild** - SQLAlchemy 2.1 made `postgresql://` select psycopg 3, which is not installed, so the first image built after its release could not open the database and the dashboard silently showed nothing. SQLAlchemy is pinned to 2.0, and the sync engines name `postgresql+psycopg2://` explicitly
- **The Gemini image generation setup check always failed** with `google-genai`; it called the old `google-generativeai` API and now creates a `genai.Client`
- **Script errors on the visual builder and the API docs page** - with no project selected the builder rendered `None` into its script and stopped, and the API docs page called a function before defining it
- **The last items of the sidebar were hidden on phones**, Documentation among them; the mobile drawer now fits the visible screen

### Security

- **Script in a widget's config** - the widget chat page put the key's config into an inline `<script>` with `json.dumps`, which leaves `</script>` intact. Whoever holds a widget's admin key could set a title that closed the script and ran their own on MATE's origin, the dashboard's, when an admin opened the widget preview. The config, agent name and key are rendered with `tojson` now
- **Script in the dashboard home's Recent Activity** - the list put an audit entry's actor, action and resource into the page unescaped. The actor can come from a chat: a widget visitor picks their own `user_id`, which an RBAC denial (and now a model fallback) records. A visitor could thus store script that ran when an admin opened the dashboard. The fields are escaped now, as on the Audit Logs page
- **The built-in admin account was not an admin to agents.** The `AUTH_USERNAME` account administers the dashboard, but its `users` row was created with only the `user` role, so on a new installation every admin-only agent refused it and no agent could write memory blocks for it. The row is now created with `admin` and `user`, and `admin` is added to an existing row that lacks it (#152)
- **Disabled webhook triggers still fired.** `is_enabled` was only checked for cron triggers, so a switched-off webhook trigger still ran when its URL was called with a valid key. `POST /triggers/{id}/fire` now answers `409` for a disabled trigger, after authentication and the signature check. Test-fire from the dashboard still runs it (#150)
- **A Tavily API key was shipped in seed data** - the V001 seed, three agent templates and the docs carried a real key in the Tavily MCP URL. They now use the `${TAVILY_API_KEY}` placeholder the MCP loader fills from the environment, and V035 swaps the key out of agents seeded with it
- **`/csp-report` and the widget allowlist were hardened** - the anonymous report endpoint refuses bodies over 16 KB before reading them, ignores malformed fields instead of failing with a `500`, and logs at most 100 violations per 10 minutes. A non-string entry in a widget key's allowed origins no longer makes the chat page a `500`, and an unrecognised `ADK_DEV_UI` value no longer turns the dev UI on in production
- **Help answers could link off-site** - a link starting with `/\`, which browsers read as `//host`, passed as a local link in the help panel; it is refused now

## [1.3.3] - 2026-09-27

Restores editing agents from the Agents page, broken since 1.3.0: the Edit, Copy
and Delete buttons there and in the visual builder did nothing. It also brings
**memory block history**: every write to a block, by an admin, the widget admin
API, an agent or a trigger, is recorded with who made it, and any version,
including a deleted block, can be restored from the dashboard.

Migration V033 applies on startup. History starts with this release; a block's
value from before it is recorded the first time the block changes.

### Added

- **Memory block history and restore** - a memory block's old value used to be gone once overwritten, whoever overwrote it: an admin, the widget admin API, a trigger, or an agent steered by a chat message. Every write now records a version with the block's label, value, description and metadata and who made it (`agent:{name} user:{id}` for an agent's tools, `widget_admin:{key id}`, `trigger:{id}`, the dashboard user, `import`). Writes that change nothing record none, and the last 20 versions per block are kept. A block written before this release gets its prior state recorded the first time it changes. The Memory Blocks modal gains **History** with **Restore**, and a list of **Deleted blocks**, which come back under their old ID. A restore is a new version and an audit entry, refused if another block took the label meanwhile. Migration V033. See `documents/DYNAMIC_MEMORY_INSTRUCTIONS.md` (#122)

### Fixed

- **Edit, copy and delete did nothing on the Agents page.** A line lost from `agent-forms.js` in 1.3.0 left the script unparseable, so none of its functions existed, the config modals included, and the browser only logged a syntax error. A test now parses every dashboard script with `node --check` when Node is installed

## [1.3.2] - 2026-09-27

A security release: every deployment should upgrade. A dynamic subagent now
keeps its parent's approvals and guardrails, not only its tool set, and the
Users page no longer runs script from a visitor's user id. It also brings
**Suggest a fix**: from a failing test case or a thumbs-down, a model proposes a
revised instruction, which is checked against the eval suite before an admin
applies it.

**Before upgrading:** a tool listed in an agent's `require_confirmation` can no
longer run inside that agent's dynamic subagents, which have no one to approve it.
If an orchestrator delegated such a tool, it now has to call it itself.

### Security

- **A dynamic subagent dropped its parent's approvals and guardrails.** The fix in 1.3.0 limited a subagent to the tools its parent holds, but not to the terms it holds them on. A tool the parent may only run after a person approves it (`require_confirmation`) reached the subagent without that requirement, and the parent's guardrails did not apply to the subagent's model at all: guardrails are looked up by agent name, and a subagent is a fresh name with no config of its own. A subagent now inherits `require_confirmation`, so a tool that needs approval cannot run inside one, and runs under its parent's input and output guardrails. Prompted by a comment on the 1.3.0 release discussion about identities regenerated mid-session
- **The Users page could run script from a user id.** Its row and button handlers put the id between single quotes with an escape that left quotes alone, and user ids include widget visitors', which the visitor partly chooses. They are now passed as JSON, as on the other pages 1.3.1 fixed
- **The remaining dashboard click handlers pass their arguments as JSON** - file search, agent builds, templates, evals, rate limits, system jobs and usage logs quoted values by hand, as the pages fixed in 1.3.1 had; the values there are set by admins. Thanks to @tayfuryldz, whose PR (#128) was the first community contribution through the new `good first issue` list

### Added

- **Suggest a fix for a bad response** - from a failing test case or a thumbs-down, a model proposes a revised instruction for the agent with a short reason. It can be edited, then checked: the agent's whole suite runs in memory with the current and the suggested instruction, results side by side and regressions highlighted. Apply is enabled only for the text that was checked and is a normal edit - a new version, an audit entry naming what prompted it, a reload - refused if the instruction changed meanwhile. Only the instruction can change, whatever the model or the request says; users' questions and comments reach the model as quoted data. Memory blocks are left alone for now: they are shared by the project's agents and not versioned. Uses `EVAL_IMPROVE_MODEL`, or `EVAL_JUDGE_MODEL`. See `documents/EVALS.md` (#112)

### Fixed

- **Ratings from the Work Room were refused for non-admins.** Rating a reply, and since 1.3.1 adding a note to a thumbs-down, posts to `/dashboard/api/feedback`, which the dashboard authz middleware did not allow for non-admins; the chat ignored the `403`, so the thumb lit up and nothing was recorded. It is now allowed alongside renaming a conversation

### Changed

- A callback branch in `AgentManager` that could never run is removed, with the `parent_agent_type` parameter only it read. Its comment claimed LLM agents under a graph ran without RBAC and guardrails; they never did, and a test now keeps it that way

## [1.3.1] - 2026-09-26

A security release: every deployment of 1.2.0 or 1.3.0 should upgrade. It also
makes evals score the version you select rather than the deployed agent, and
turns a user's thumbs-down, with their note on what was wrong, into an eval test
case in one click.

**Before upgrading:** the server-side browser now refuses private, loopback and
link-local addresses. If your agents browse an intranet, set
`BROWSER_ALLOW_PRIVATE_NETWORK=true`. Signed-in users who are not admins keep the
Work Room but can no longer call the dashboard's admin APIs.

### Security

Details, and what to rotate after upgrading, are in the security advisory [GHSA-35hg-8vcr-5h6h](https://github.com/antiv/mate/security/advisories/GHSA-35hg-8vcr-5h6h).

- **The interactive browser websocket required no login.** It now requires a signed-in session and serves only that user's own browser. The server-side browser, including agents' browser tools, no longer loads private, loopback or link-local addresses (`BROWSER_ALLOW_PRIVATE_NETWORK=true` lifts this for intranet deployments)
- **Chat replies could run script.** The widget, Work Room and standalone chat now escape an agent's reply before rendering its markdown, and allow only http(s) links. The dashboard no longer keeps the password in the browser
- **A signed-in user who was not an admin could act as another user** on the agent server, and **read admin-only dashboard data**. The proxy now pins a non-admin to their own user id, and dashboard reads require admin outside the Work Room's own routes
- **Artifacts were readable without a login.** They now require the widget's key or a login, for the owner or an admin
- Agent changes made through the widget admin API are now versioned and audited
- **RBAC let a new user's first denied request through.** The ADK RBAC callback allowed any request whose check raised, "to prevent system breakage". A newly created user came back from `get_or_create_user` detached from its session, so reading its roles to log a denial raised — and the denial became an allow. The first request of every new user to an agent they may not use went through, which under the secure default includes every agent with no roles configured. The new user is now loaded before its session closes, and both runtimes deny when the check cannot complete; the LangGraph hook had copied the fail-open deliberately
- **An agent's endpoint key reached the browser and the model.** 1.2.0 masked the key in the export, but the agent list that `GET /dashboard/api/agents` returns and the Agents page embeds, the version history, the rollback response and the `read_agent` tool all carried it in clear. The list and history are readable by any signed-in user, not only admins, and `read_agent` hands its result to the model. All four now return the same `__stored__` sentinel the edit form already sends back to keep the stored key; `${VAR}` references still show as written. Stored keys and version snapshots are unchanged, so saves, rollbacks and evals keep using the real key

### Added

- **Turn a thumbs-down into an eval test case** - a 👎 used to show up only as a number in the satisfaction rate, with no way to see which responses earned it. The Evals page now lists rated-down responses with the user's question, the agent's answer and the visitor's comment, read back from the session by invocation id on either runtime. **Add to evals** opens the test case form prefilled with the agent and the question, defaulting to `llm_judge`, with the rated answer shown for reference. A test case records the rating it came from (`test_cases.source_feedback_id`, migration V032), so the same response is not added twice. The list is admin-only. See `documents/EVALS.md` (#109)
- **Say what was wrong after a thumbs-down** - the rating endpoints always accepted a comment, but no chat sent one, so the owner learned *that* an answer was bad and never *why*. After a 👎 the widget and the Work Room now show an optional note under the message. The rating is recorded on the click as before; a note, if sent, is added to the same rating. Skip, Escape or an empty note close it, and switching to 👍 removes it. Translated into all 17 widget languages

### Fixed

- **"Score against version" scored the deployed agent.** Evals called the live runtime by agent name and filed the result under whichever version was selected, so scoring version 1 while version 2 was live scored version 2 — and the score history and regression alert compared numbers that were only right for the version active at the time. The agent is now built from the selected version's snapshot and run in memory, the way ephemeral subagents run, without touching `agents_config` or the deployed agent. Its sub-agents come from their current config. On the LangGraph runtime this returns `501` instead of silently running the deployed agent. See `documents/EVALS.md` (#111)
- **Evals were refused on admin-only agents.** An agent with no roles configured is admin-only, and evals ran as a plain `eval_runner` user, so RBAC refused them and the eval scored the refusal instead of an answer. Eval runs are started by an admin and now skip RBAC, keyed on a context variable set only inside the in-process run
- **An agent's own endpoint was ignored on the ADK runtime.** `initialize_agent_from_config` builds the dict it hands to `create_model_from_agent_config` field by field, and `model_base_url` and `model_api_key` were not among the fields. An agent pointed at an OpenAI-compatible endpoint (1.2.0) therefore reached the provider's default host with the provider env key instead, on the default runtime. The LangGraph runtime was not affected. The tests exercised `create_model_from_agent_config` directly, never the build path the runtime uses; one now does
- **Ratings in standalone builds went nowhere.** A standalone build uses the Work Room's chat script, which offered 👍/👎 and posted them to `/dashboard/api/feedback` — a route the standalone server does not have. Every rating was a silent 404, while the thumb lit up as if it had been recorded. A standalone build has no dashboard to read ratings in either, so the buttons are now not shown there; the Work Room is unchanged (#113)

## [1.3.0] - 2026-09-26

A MATE agent can now sit behind the coding tool you already use. OpenCode, Cline,
Continue and Roo can call it as a model over `/v1/chat/completions` — including
their own client-side tools and screenshots — while RBAC, rate limits, cost
tracking and guardrails still apply. An orchestrator can also fan work out to
short-lived subagents that never hold more tools than it does.

### Security

- **A subagent can no longer exceed the tools its parent holds.** `_build_subagent_tools` equipped a child with any tool family named in a subtask, defaulting to enabled when the parent had no such entry — so the orchestrator's prompt decided the child's privileges rather than the parent's configuration. Since that prompt can be influenced by anyone talking to the agent, a child with `execute_shell_command` running as the server user was one injected instruction away. The widget guard did not help: it keys off the agent name, and a subagent gets a fresh one, so an agent refused the executor for being widget-exposed could obtain it on a child. The same route granted `delete_agent` and `place_order`. Child tools are now intersected with the parent's effective set, with the widget refusal inherited. Reported privately against 695c735 by Rafael Asor (Attenu), https://github.com/attenu-io/attenu-guard. No released version was affected: the delegation tool landed after v1.2.0 was tagged, so the issue existed only on `main`

### Added

- **Dynamic subagent delegation** (`subagent_delegation`) - an orchestrator agent can spawn ephemeral subagents and run them in parallel within a single turn. They execute in memory via `InMemorySessionService` and an ADK `Runner`, so they create no rows in `agents_config`; each is equipped only with the tools its subtask needs; and only the distilled result returns to the parent, keeping raw search and tool output out of the root agent's context window. Token usage is recorded against the parent session and user. Configurable per agent from the dashboard. See `documents/DYNAMIC_SUBAGENTS.md`
- **Real-time web search** - `google_search` uses the Tavily API when `TAVILY_API_KEY` is set, and falls back to DuckDuckGo when it is not
- **Client-side tool calling on the OpenAI bridge** - `/v1/chat/completions` now accepts `tools` and `tool_choice`, so an external coding agent (OpenCode, Continue, Cline) can hand a MATE agent the tools that only run on the caller's machine. They are declared to the model *alongside* the agent's own tools; when the model calls one, MATE emits OpenAI `tool_calls` with `finish_reason: "tool_calls"` and pauses the turn, and the caller's `role: "tool"` reply resumes it. Parallel calls are supported. Both runtimes carry it: on ADK a `ClientToolset` resolves the caller's declarations off `RunConfig.custom_metadata` into long-running tools on every LLM step, so no agent is rebuilt and no cache is invalidated; on LangGraph they become tools whose body interrupts the graph, with the compiled-graph cache keyed by the declared tool set. Only the exposed root agent receives them — a sub-agent must never hold more than its parent — and the agent's own tool wins any name collision. Requests without `tools` behave exactly as before. See `documents/OPENAI_COMPATIBILITY.md`
- **Rate limits on the OpenAI bridge** - with `RATE_LIMIT_ENABLED=true`, the per-user, per-agent and per-project limits and token budgets now apply to `/v1/chat/completions` as they already did to the dashboard chat and the widget. The user is the PAT owner and the project is the exposed agent's project; a blocked request gets `429` with a `Retry-After` header. One chat-completions request counts once against `requests_per_minute`, even when a tool-calling turn fans out into several runtime calls. See `documents/RATE_LIMITS.md`

### Changed

- **google-adk upgraded from 2.3.0 to 2.9.2**, and `google-genai` is now constrained to `>=2.19.0`. If you pin dependencies downstream, move both together

### Fixed

- **Image uploads were refused for models that can see perfectly well.** `model_supports_vision()` matched a handful of hardcoded substrings (`gemini`, `gpt-4o`, `claude-3`, `-vl`) and refused everything else, so the widget and proxy answered a legitimate upload with `400 The configured model does not support vision/images` for `gpt-4-turbo`, `gpt-4.1`, `claude-sonnet-4-5`, `llama-4`, `pixtral`, `gemma-3` and every model released after the list was written. Capability now comes from LiteLLM's model map, which MATE already depends on and which refreshes itself, and an image is held back only on positive knowledge that the model is text-only — anything unknown is allowed through for the provider to judge. A few genuinely text-only models LiteLLM has no opinion about (`gpt-3.5-turbo`, `deepseek-chat`) now fail at the provider instead of at MATE. The `/v1` bridge also stopped running this lookup on every request; it runs only when the turn actually carries an image

- **Screenshots sent to the OpenAI bridge were dropped on the floor.** A client attaching an image sent an `image_url` content part, and the bridge's text extraction kept only `type: "text"` — so the agent answered about a screenshot it had never received. Images now reach the runtime as `inline_data`, which both runtimes already consume. Only `data:` URIs are accepted (fetching a caller-supplied URL server-side would be an SSRF), images over 5 MB are refused, and anything that cannot be forwarded — including to an agent whose model has no vision support — is described to the agent instead of vanishing

- **Every conversation through the OpenAI bridge shared one session.** The agent session id was keyed on the md5 of the *first* message, which for any coding agent is a constant system prompt — so all of a user's conversations with an agent collapsed into a single session and leaked context into each other. The id now derives from the agent and the first *user* message, and deliberately not from the system prompt, which Cline and Roo rewrite when you toggle Plan and Act. A client that tracks its own conversations can pin the session outright with the `X-MATE-Conversation-Id` header. The `openai_sess_` prefix is unchanged, so usage still classifies as `api`
- **`usage` on the OpenAI bridge reported the whole conversation.** Tokens were summed across every row sharing the session id, so the figure grew monotonically and was wrong from the second turn on. It is now summed over the invocation ids of the turn actually being answered
- **Every answer on the OpenAI bridge arrived twice.** Both runtimes stream a segment as `partial` frames carrying deltas and then one final frame carrying the whole text; the bridge's delta tracker assumed the partials were cumulative, so the final frame did not extend what it had seen and was emitted again in full. The tracker now goes by the `partial` flag — deltas pass through, and the final frame contributes only what was never streamed — and treats an unflagged stream as cumulative, as before
- **Switching a conversation to a MATE agent mid-way failed the turn.** OpenCode lets you change model inside a conversation; the new agent then received a transcript ending in a `role: "tool"` result for a call another model had made, the bridge forwarded it as a function response, and ADK refused the whole message with `Function call not found for function response ids`, surfaced to the client as a generic stream error. The bridge now looks at what the agent's session actually holds before building the turn: a session with no history gets the transcript replayed once as text, and a tool result for a call the session never made is passed as text rather than as a function response
- **An assistant message with `content: null` was rejected.** Clients replay their own history, and an assistant message that only makes tool calls has no content, so the whole request failed validation with a 422
- **Streamed text could be duplicated.** The bridge's delta bookkeeping appended to the accumulator instead of replacing it, so after a segment that did not extend the previous one every later comparison failed and text was re-sent. The corrected logic is shared with `agent_invoke`
- **A runtime that refused the connection raised `UnboundLocalError`** from the stream's `finally` block instead of reporting the failure
- **Building an agent from the visual builder logged a spurious File Search traceback.** The builder stores `file_search` as a bare boolean toggle, while the tool factory expected a `{"enabled": ...}` dict and failed with `AttributeError: 'bool' object has no attribute 'get'`. A boolean is now normalised to the dict form

## [1.2.0] - 2026-09-02

MATE's control layer no longer requires that you built your agents in MATE, and
EU AI Act Art. 50 obligations — which applied from 2 August 2026 — are now met by
default on public chat surfaces.

### Added

- **External agents** - an agent can point at an OpenAI-compatible endpoint you already run, via `model_base_url` and `model_api_key` on the agent row. RBAC, guardrails, evals, config versioning, audit, cost tracking and the widget then apply to it like any other agent, with no new runtime paths. Both the ADK and LangGraph runtimes resolve the endpoint through the same function. See `documents/EXTERNAL_AGENTS.md`
- **MCP over HTTP** - an external MCP server entry with a `url` is reached directly over streamable HTTP (or `sse` for older servers), instead of spawning `npx mcp-remote` as a subprocess. `command`/`args` stdio servers are unchanged. See `documents/MCP_SERVERS.md`
- **`${VAR}` secrets** - any string in an MCP server entry, and an agent's endpoint key, may reference the server environment as `${VAR}`, keeping credentials out of the database and out of config exports. An unset variable skips the server or refuses to build the agent rather than sending the placeholder as a credential
- **AI disclosure (Art. 50)** - the widget and standalone builds tell people they are talking to an AI. Wording is per-agent and translatable; there is no on/off switch, only a waiver field that hides the notice and records why, so the decision and its justification cannot come apart. Setting or clearing a waiver writes its own audit entry. See `documents/AI_ACT.md`
- **Marking of generated images (Art. 50(2))** - generated PNGs carry an XMP packet declaring the IPTC digital source type `trainedAlgorithmicMedia`, written before the artifact is saved so every copy carries it. All three image generation paths mark
- **`documents/AI_ACT.md`** - what applies when, and what MATE does and does not do about it
- **Integration test against a live endpoint** - a stub OpenAI-compatible server on localhost proves the configured host, credential and `usage` object actually cross the wire. Streaming needed no translation layer; the test asserts that rather than assuming it

### Changed

- **`MCPToolset` renamed to `McpToolset`** - ADK 2.3 keeps the old name as a subclass that only warns and delegates, so this is behaviour-identical and silences the DeprecationWarning
- **Compliance wording corrected** - `audit_service` described itself as "for EU AI Act compliance" and the README called the audit log "EU AI Act retention-aware". An append-only log is *evidence toward* Art. 12 record-keeping; compliance is a property of a deployed system and its operator, not of a log

### Fixed

- **README demo links** - the murder mystery link was published without its widget key, so the page rendered with nothing to interact with, and defaulted to Serbian. The section headline pointed at the site root, which redirects to an authenticated dashboard, so a visitor arriving from GitHub met a login form

### Security

- **A provider key no longer follows an agent to a third-party host.** An agent with a custom `model_base_url` and no key of its own is given a placeholder rather than left to LiteLLM's fallback, which would have read `OPENAI_API_KEY` and sent it to whatever that URL names
- **An unresolvable `${VAR}` refuses to build the agent** instead of falling back, for the same reason
- **Stored endpoint keys never reach the browser** - API responses carry a sentinel, and saving a form with the sentinel unchanged leaves the stored value alone. A `${VAR}` reference is not a secret and is shown as written. Templates carry an endpoint but never a credential

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
