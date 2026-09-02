**Title:** Two weeks, nine merged PRs, zero new features — the bugs that were most embarrassing to find

---

We spent the last two weeks doing nothing but fixing things in **MATE (Multi-Agent Tree Engine)**. No new page, nothing to demo. Nine PRs merged; the test suite went 682 → 768.

Almost none of these were regressions. They had been in the code since the day it was written, and the reason they survived is the interesting part: **every one of them looked like it was working.**

## The ones where nothing was visibly wrong

**The Usage page was showing numbers we made up.** Three columns of the agent performance table — average response time, success rate, last used — were literal strings in the template. Every agent rendered `~1.2s`, `98.5%` and `2 hours ago`, identical for every row, with a hardcoded green badge. They now come from `agent_responses`, which has been recording `duration_ms` and `status` all along. Agents with no measurements render `—` and the badge takes its colour from the value. A fabricated number is worse than an absent one — that was the whole point.

**`code_executor` was not blocked on widget-exposed agents.** The module's own docstring says the tool is not a sandbox and to keep it off public agents. Nothing enforced that sentence, so enabling it on an agent that also had a widget key handed anonymous visitors on the embedding site shell execution as the server user. It is now refused when a widget key names the agent — active or not, since reactivating a key is a dashboard toggle that never touches the agent config. The check fails closed: database unreachable means refused, not granted. Only `code_executor` is withheld, so the agent degrades instead of breaking. `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET=true` restores the old behaviour.

The most honest line in this post: **the demo agent shipped in this repo had two widget keys on it.** The configuration the guard exists to catch was our own.

**Template deletion was never written to the audit log.** The call site invoked `audit_service.log_event(...)` — a function that does not exist. Every deletion raised `AttributeError` into a bare `except` that logged a warning and moved on. For an append-only log whose docstring says "EU AI Act compliance", that is the worst possible failure shape: the log looks complete, and a successful delete gives no hint its record was dropped.

**The other half of that trail was missing too.** Deletes were logged; bulk creates were not — none of the four import/sync endpoints called the audit service. `agents/import` with `overwrite=true` can rewrite every agent in an installation and left no record of who did it. `ACTION_TEMPLATE_IMPORT` had existed as a constant referenced by nothing: the intent was there, the call never was. All four endpoints now audit, with the actor's IP and the blast radius in the row.

**Agents built without a description delegate to nowhere.** ADK routes delegation on the `description=` field. It is nullable on `agents_config` and nothing required it, so an agent could build fine, appear in the tree, and silently never be delegated to. It now warns at build time, scoped to agents that have sub-agents or are one. We deliberately did not invent a fallback description — steering ADK's routing with text nobody wrote is worse than a warning.

## The one where dict ordering decided whether your import was corrupted

Four copies of a name-substitution helper rewrote agent names with a loop of `str.replace`. Sequential replacement rewrites what a previous replacement produced (`{alpha: beta, beta: gamma}` turns `alpha` into `gamma`), and it has no notion of a whole word — with agents named `support` and `support_billing`, mapping `support` first also rewrites the middle of the longer name.

Which one bit you depended on `dict` iteration order. That is exactly why it survived: intermittent, and silent — the corruption lands inside instructions and tool configs at import time and nothing reports it. All four call sites now share one helper: single pass, `\b` boundaries, alternation ordered longest-first, names `re.escape`d.

## Triggers, in three steps

**Webhook triggers could not be told anything.** The fire endpoint never read the request body, so the agent always saw the prompt stored at creation time — which makes a webhook "run this text on demand" and useless for "this issue changed". Prompts now interpolate the firing body: `{{ payload }}`, `{{ payload.key }}`, `{{ payload.issue.fields.summary }}`, `{{ payload.commits.0.id }}`. Rendering is forgiving in both directions on purpose: a prompt with no placeholder is returned untouched, and a body that is absent or not JSON fires with no payload rather than erroring. Substitution is single-pass, so a body containing `{{ payload.x }}` is inserted as literal text.

**Which immediately made the fire key too weak to carry that endpoint.** Whoever holds a shared secret now controls text going straight into an LLM prompt, on an endpoint designed to be called by third parties — and a bearer secret cannot prove who composed a body. So: optional per-trigger HMAC-SHA256 over the raw body, off by default, accepting `sha256=` in `X-Hub-Signature-256` (GitHub/GitLab shape) or a bare digest in `X-MATE-Signature`. The endpoint reads raw bytes before anything parses them — re-serialising parsed JSON does not reproduce the signed bytes. With verification required, a valid fire key and no signature is **401**. `?key=` in the URL still works but now logs a deprecation warning.

**And two trigger types that could never fire were still on offer.** `file_watch` and `event_bus` were accepted by the model and listed in the dropdown while the runner skipped them as "not yet implemented" — so you could save something that looked like automation and silently never ran. Creation is now refused with 400 at the API, not just hidden in the UI. The options are disabled rather than deleted: removing them would leave a legacy row's type absent from the `<select>`, and editing that row would quietly convert a dead `file_watch` trigger into a live `cron` one.

## Two plain papercuts

`python shared/migrate.py` **has never worked** — relative import in a file invoked as a script, dying on `ImportError` before doing anything, while that exact invocation is documented in three places including the README.

And `agents/import` blamed the wrong thing for every failure: the handler raised its own `HTTPException` inside a `try` whose `except Exception` rewrapped it, so every import failure surfaced as `Invalid JSON data: 400: ...`, naming the request body as the cause of failures that had nothing to do with parsing it.

## If you're updating

- **Run migrations** (`python shared/migrate.py run` — which now actually runs). `V029` adds the trigger signing columns.
- **Nothing changes by default.** Signature verification is opt-in per trigger, prompts without placeholders render bit-for-bit unchanged, and already-stored `file_watch`/`event_bus` rows keep listing and can still be disabled.
- **Check whether any agent with a widget key has `code_executor` enabled** before updating, or it will be refused on the next agent reload.

## The takeaway

One pattern runs through all of it: **the bugs that lasted longest were the ones that produced a plausible-looking result.** A hardcoded `98.5%`. An audit log complete except for the half nobody checks. A guard documented in a docstring and enforced nowhere. None of them threw, none showed up in a log, and every one was found by someone asking "where does this number actually come from?"

That question is free, and we clearly weren't asking it often enough.

Repo: https://github.com/antiv/mate — if you find something like this in there, it's a good day for us when you say so.
