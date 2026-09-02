**Title:** Our dashboard was full of numbers the database never recorded. Fifteen PRs later, here is every place we found it.

---

Two and a half weeks on **MATE (Multi-Agent Tree Engine)**, fifteen merged PRs, test suite 590 → 768. Some of it is new (alerting, response feedback, cloning an agent tree between projects), but the thread running through almost all of it is one shape of bug, and it is worth more than the changelog:

**Something rendered a value. Nothing had ever produced it.**

Not crashes. Not stack traces. Plausible-looking output with nothing behind it. Here is every instance we dug out.

**TL;DR** — On PostgreSQL 18 of 26 migrations had been failing silently for months (the ORM created the schema anyway, so everything looked fine). The `ERROR` status our alerting needed was never written by anything. Latency did not exist as a column. The agent performance table's three metrics were hardcoded strings. Template deletion called an audit function that does not exist, and imports were not audited at all. `code_executor` was documented as unsafe on public agents and enforced nowhere. All fixed, plus alerting, response feedback, latency percentiles, agent-tree cloning, and webhook payloads with HMAC verification. Details below.

## The worst one: on PostgreSQL, our migrations were decorative

It started as a small report — the migrations page on a deployed PostgreSQL instance read `025` after we shipped a `V026`. The migration was failing and rolling back, so it never got recorded.

Patching that one file took a couple of lines. Then we asked the follow-up question, and it was much worse. Running every migration through the real code path against an empty PostgreSQL 16 database:

| | applied | failed |
|---|---|---|
| before | 8 | **18** |
| after | **26** | 0 |

`_split_sql_statements` split on every semicolon outside a quoted string. A PL/pgSQL function body is full of semicolons, so `CREATE OR REPLACE FUNCTION ... AS $$ BEGIN NEW.updated_at = NOW(); ...` got cut at the first inner `;` — `unterminated dollar-quoted string`, V001 fails, and every later migration that expected V001's tables fails with it. The single guard against this was `if 'DO $$' in sql_content: return [whole_file]`, which is why a handful of files survived while nothing inside them could be split at all.

**And nobody noticed for months, because `Base.metadata.create_all` runs on startup.** The ORM created the schema regardless, so every page rendered and every feature worked. What silently did not happen was everything a migration does *beyond* DDL — data backfills, triggers, constraints. A failed migration is logged and swallowed; startup continues.

Dollar-quoted blocks are now scanned to their matching tag (`$$` and tagged `$func$`), quoted literals are copied whole, and the `DO $$` special case is gone. 26/26 apply. If you run MATE on PostgreSQL, this is the reason to update.

## You cannot alert on a signal that does not exist

We set out to build alert rules — agent error rates, guardrail bursts, budget overruns. Verifying the issue against `main` found the blocker immediately: `token_usage_logs.status` has always declared `SUCCESS | ERROR | ACCESS_DENIED`, and **nothing ever wrote `ERROR`.** On the ADK path a model failure produced no row at all: the after-model callback is never invoked when the model raises, and it is gated on usage metadata a failed response does not carry.

So "alert me when this agent is failing" had nothing to read. Recording failures had to ship first (via ADK 2.3's `on_model_error_callback`, which also closes a GenAI span that had been leaking on every failed model call), and alert rules second.

That dig turned up a regression nobody had reported: **none of the four usage analytics aggregates filtered by status**, so `ACCESS_DENIED` rows had been inflating request counts, unique users and top-agent rankings while contributing no tokens. Fixed — which means reported usage will *drop* for anyone with RBAC denials in their history. That is a correction, but it will look like a change.

Alerts themselves are a table plus a dashboard page, evaluated periodically on the scheduler rather than inline. Cooldown is claimed with a conditional `UPDATE`, so it survives a restart and two processes cannot both deliver — the old half-existing budget alert deduped through a process-local dict and ran a blocking 10s HTTP POST inline on every successful model response. Set `ALERTS_ENABLED=true`; nothing fires without it.

## There was no latency either

Same story one layer over. We wanted latency and cost per conversation. Verification found three gaps: no per-model pricing exists anywhere in MATE (so it ships as *tokens*, not money), no duration column exists at all, and ADK wrote `request_id = uuid4()` per model call, so a response's several calls could not be grouped.

Where to measure was the real decision. Eight surfaces produce an agent response, and **only two of them reach the auth server's proxy** — the widget, `/v1/chat/completions`, MCP, triggers, Slack and evals each open their own client straight to the agent port. Measuring in the HTTP layer would have missed six of eight. It sits at the runtime's invocation boundary instead.

Rows are opened before the run and closed after it, because ADK calls its after-run hook after the event loop rather than in a `finally` — under a single-write design a failed invocation recorded nothing, and the first live probe returned 200 with an empty table. A row left `RUNNING` is now a response that errored or never finished.

Plus thumbs up/down on responses, keyed by `(session_id, invocation_id)` so re-rating changes the row and the satisfaction rate counts responses, not clicks. Satisfaction is always shown with its denominator (`4 of 11 rated`) — ratings come from two surfaces only, and a bare percentage over a thin self-selected sample reads as far more than it is.

Percentiles cover interactive traffic by default, with a selector for Triggers / Slack / Evals / All. A scheduled trigger taking four minutes must not define the p95 a customer reads, but it should not be invisible either — with seeded data, `all` moves p95 from 8.8s to 240s.

## And then we found the table that was just lying

Three columns of the agent performance table — average response time, success rate, last used — were **literal strings in the template.** Every agent rendered `~1.2s`, `98.5%` and `2 hours ago`. Identical for every row, hardcoded green badge, no data behind any of it.

They now read from the tables the work above created. Agents with no measurements render `—`, and the badge takes its colour from the value. A fabricated number is worse than an absent one; that was the whole point.

## The audit log was complete except for the half nobody checks

Template deletion called `audit_service.log_event(...)` — a function that does not exist. Every deletion raised `AttributeError` into a bare `except` that logged a warning and moved on. For an append-only log whose docstring says "EU AI Act compliance", that is the worst possible failure shape.

Then the other half: deletes were logged, bulk creates were not. None of the four import/sync endpoints called the audit service. `agents/import` with `overwrite=true` can rewrite every agent in an installation and left no record of who did it. `ACTION_TEMPLATE_IMPORT` had sat there as a constant referenced by nothing — the intent was written, the call never was. All four now audit, with actor IP and blast radius.

## Two guards that existed only as prose

**`code_executor` on widget-exposed agents.** The module's own docstring says the tool is not a sandbox and to keep it off public agents. Nothing enforced that sentence, so enabling it on an agent that also had a widget key handed anonymous visitors on the embedding site shell execution as the server user. Now refused when a widget key names the agent — active or not, since reactivating a key is a toggle that never touches the agent config — and it fails closed if the database is unreachable. Only that tool is withheld, so the agent degrades instead of breaking.

The honest part: **the demo agent shipped in this repo had two widget keys on it.** The configuration the guard exists to catch was our own.

**Agents with no description.** ADK routes delegation on the `description=` field. It is nullable and nothing required it, so an agent could build fine, appear in the tree, and silently never be delegated to. It warns at build time now. We deliberately did not invent a fallback description — steering ADK's routing with text nobody wrote is worse than a warning.

## The bug that dict ordering decided

Four copies of a name-substitution helper rewrote agent names with a loop of `str.replace`. Sequential replacement rewrites what a previous replacement produced (`{alpha: beta, beta: gamma}` turns `alpha` into `gamma`), and it has no notion of a whole word — with agents named `support` and `support_billing`, mapping `support` first also rewrites the middle of the longer one.

Which one bit you depended on `dict` iteration order. Intermittent, and silent: the corruption lands inside instructions and tool configs at import time and nothing reports it. Found while building tree cloning, which had solved it inline; all four call sites now share that one helper — single pass, `\b` boundaries, longest name first, `re.escape`d.

**Cloning itself** shipped in the same stretch: a row action on root agents copies a whole hierarchy into another project, with an editable suffix (names are globally unique across projects, so every clone needs renaming — not just collisions), memory blocks copied, file-search assignments pointed at the same remote store rather than re-uploading every document. Insert-only, so "without corrupting the source" holds by construction rather than by care.

## Triggers, in three steps

**They could not be told anything.** The fire endpoint never read the request body, so the agent always saw the prompt stored at creation time — fine for "run this text on demand", useless for "this issue changed". Prompts now interpolate the firing body: `{{ payload }}`, `{{ payload.issue.fields.summary }}`, `{{ payload.commits.0.id }}`. Forgiving in both directions on purpose — no placeholder means the prompt is returned untouched, and a missing or non-JSON body fires with no payload rather than erroring.

**Which made the fire key too weak to carry that endpoint.** Whoever holds a shared secret now controls text going straight into an LLM prompt, on an endpoint designed for third parties — and a bearer secret cannot prove who composed a body. Optional per-trigger HMAC-SHA256 over the raw body, off by default, accepting `sha256=` in `X-Hub-Signature-256` (GitHub/GitLab shape) or a bare digest in `X-MATE-Signature`. Raw bytes are read before anything parses them; re-serialising parsed JSON does not reproduce the signed bytes. With verification on, a valid fire key and no signature is **401**.

**And two trigger types that could never fire were still on offer.** `file_watch` and `event_bus` were accepted by the model and listed in the dropdown while the runner skipped them as "not yet implemented" — you could save something that looked like automation and silently never ran. Refused with 400 at the API now, not just hidden in the UI. The dropdown options are disabled rather than deleted: removing them would leave a legacy row's type absent from the `<select>`, and editing that row would quietly convert a dead `file_watch` trigger into a live `cron` one.

## Two plain papercuts

`python shared/migrate.py` **has never worked** — relative import in a file invoked as a script, dying on `ImportError` before doing anything, while that exact invocation is documented in three places including the README.

And `agents/import` blamed the wrong thing for every failure: the handler raised its own `HTTPException` inside a `try` whose `except Exception` rewrapped it, so every failure surfaced as `Invalid JSON data: 400: ...` — naming the request body as the cause of failures that had nothing to do with parsing it.

## If you are updating

- **PostgreSQL users: expect a batch of migrations to apply on the next restart**, including ones that have never applied. This is the fix working, not something going wrong. Check the migrations page afterwards.
- New migrations `V026`–`V029` (alert rules, response metrics, feedback, trigger signing).
- **Alerts stay dormant until `ALERTS_ENABLED=true`.** Look for `TriggerRunner: registered alert evaluation every 60s` in the startup log.
- **Usage figures may drop** if you have RBAC denials in your history — denied requests no longer count as traffic.
- **Check whether any agent with a widget key has `code_executor` enabled** before updating, or it will be refused on the next agent reload.
- Signature verification is opt-in per trigger; prompts without placeholders render bit-for-bit unchanged.

## The takeaway

Every single one of these produced a plausible-looking result. A migrations page reading `025` on a database where two thirds of the migrations had never run. A status column declaring `ERROR` that nothing ever wrote. A hardcoded `98.5%`. An audit log missing exactly the half nobody audits. A safety rule that existed only as a sentence in a docstring.

None of them threw. None of them showed up in a log. Every one was found by someone going and asking **"where does this number actually come from?"** — and then not accepting "the page renders, so it works" as the answer.

That question is free. We clearly had not been asking it often enough.

Repo: https://github.com/antiv/mate — if you find another one of these in there, it is a good day for us when you say so.
