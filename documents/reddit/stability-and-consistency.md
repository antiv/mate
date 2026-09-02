**Title:** Two weeks on stability in MATE — reliable PostgreSQL migrations, consistent metrics, hardened triggers (15 PRs)

---

We spent the last two weeks on **MATE (Multi-Agent Tree Engine)** doing a focused stability pass: making the numbers in the dashboard consistent with what the database actually records, making migrations behave the same on every supported engine, and closing gaps in the trigger and audit paths. Fifteen PRs merged, test suite 590 → 768.

A few new things landed along the way, mostly because the stability work made them possible.

## Migrations now apply reliably on PostgreSQL

The biggest single improvement. The SQL splitter cut statements on every semicolon outside a quoted string, which does not survive a PL/pgSQL function body — so on PostgreSQL a good number of migrations were rolling back and continuing quietly. The schema still looked right, because SQLAlchemy's `create_all` builds it on startup, but anything a migration does *beyond* DDL — backfills, triggers, constraints — was not happening.

Dollar-quoted blocks are now scanned to their matching tag (`$$` and tagged `$func$` forms alike) and quoted literals are copied whole. On an empty PostgreSQL 16 database, migrations went from 8 of 26 applying to **26 of 26**. If you run MATE on PostgreSQL, this is the update to take.

## The metrics are now measurements

Several dashboard figures were either placeholders or computed from data that did not fully exist. That is now consistent end to end:

- **Response latency and tokens per conversation** are recorded at the runtime's invocation boundary, so all eight surfaces that produce a response are covered — the widget, `/v1/chat/completions`, MCP, triggers, Slack and evals all bypass the auth proxy, so measuring in the HTTP layer would have caught only two of them.
- **The agent performance table** (average response time, success rate, last used) now reads real per-invocation data instead of template placeholders. Agents with no measurements render `—` rather than a number, and the success badge takes its colour from the value.
- **Failed model calls are recorded.** ADK's after-model callback never fires when the model raises, so failures left no row at all; `on_model_error_callback` closes that gap (and a GenAI span that had been leaking on every failed call).
- **Usage aggregates filter by status.** Denied requests were counting toward request totals, unique users and top-agent rankings. Worth knowing before you update: reported usage may drop if you have RBAC denials in your history.
- **Latency percentiles are filterable by origin** — interactive by default, so a four-minute scheduled trigger does not define the p95 a customer reads, with Triggers / Slack / Evals / All available when you want them.

Plus **thumbs up/down on responses**, keyed per invocation so re-rating updates in place, and satisfaction always shown with its denominator (`4 of 11 rated`) since ratings only arrive from two surfaces.

## Alerting

New alert rules on agent error counts, guardrail bursts and budget thresholds, with a dashboard page and configurable cooldown. Evaluation runs periodically on the existing scheduler rather than inline on each request, and the cooldown is claimed with a conditional `UPDATE`, so it survives restarts and two processes cannot both deliver. Enable with `ALERTS_ENABLED=true`.

## Triggers

Webhook triggers can now be told what happened: the firing body is interpolated into the prompt via `{{ payload }}`, `{{ payload.issue.fields.summary }}`, `{{ payload.commits.0.id }}`. Prompts without placeholders render unchanged, and a missing or non-JSON body fires with no payload rather than erroring.

Since that puts caller-supplied text into an agent prompt, **optional HMAC-SHA256 verification** came with it — off by default, accepting `sha256=` in `X-Hub-Signature-256` (GitHub/GitLab shape) or a bare digest in `X-MATE-Signature`, verified over the raw body before parsing.

Two trigger types that were listed in the UI but never implemented (`file_watch`, `event_bus`) are now refused at the API rather than saved as automation that silently never runs. Existing rows still list and can still be disabled.

## Consistency and hardening elsewhere

- **Agent name substitution** during template import and cloning is now a single word-boundary pass shared by all call sites, so nested names like `support` and `support_billing` can no longer be rewritten into each other.
- **Clone a whole agent hierarchy into another project** — root-agent row action, editable suffix, memory blocks copied, file-search assignments pointed at the same remote store. Insert-only, so the source is untouched by construction.
- **The audit trail covers creates as well as deletes.** Template deletion and all four import/sync endpoints now write rows with the actor's IP and the scope of the change.
- **`code_executor` is refused on agents that have a widget key**, matching what the module's own documentation always said. `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET=true` if you need the old behaviour.
- **A warning when a database-backed agent is built without a description**, since ADK routes delegation on that field.
- **`python shared/migrate.py` works** in the form the README documents, and agent import failures now report their own reason instead of a generic JSON parse error.

## If you are updating

- PostgreSQL users: expect a batch of migrations to apply on the next restart, including ones that had not applied before. Check the migrations page afterwards.
- New migrations `V026`–`V029` (alert rules, response metrics, feedback, trigger signing).
- Alerts stay dormant until `ALERTS_ENABLED=true` — look for `TriggerRunner: registered alert evaluation every 60s` at startup.
- Check whether any agent with a widget key has `code_executor` enabled before updating.
- Signature verification is opt-in per trigger; existing trigger prompts are unaffected.

Repo: https://github.com/antiv/mate — issues and PRs welcome.
