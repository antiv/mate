**Title:** A reader emailed me a path traversal in MATE — it turned into a full security audit. What changed, and the 3 things you have to do after updating.

---

Someone reading the code found an arbitrary file read in the agent-builder endpoints and emailed me a clean report. I fixed it, then spent a day auditing everything around it instead of just that one handler. The traversal was real — but it was not the worst thing in there. Everything through medium severity is now fixed in one PR.

## What was hardened

**Authorization.** Dashboard *pages* checked whether you were an admin. The JSON APIs behind them did not — 57 of 66 write endpoints only required *some* logged-in session, including user management. There is now a deny-by-default middleware: every mutating `/dashboard/api/*` request requires admin unless it is on an explicit allowlist (currently one route). New endpoints are protected automatically instead of by remembering.

**Identity.** The admin decision no longer trusts a profile field that the account holder controls at the identity provider. Admin now comes from a provider-verified identity or the `admin` role in the database. Optional `OAUTH_ALLOWED_DOMAINS` / `OAUTH_ALLOWED_EMAILS` were added — SSO signup was open to anyone with a Google/GitHub account.

**Path handling.** Every endpoint that builds a filesystem path from request input now resolves it and refuses anything outside its base directory: builder endpoints, agent folder create/delete, the local artifact service, and the self-building `create_agent` tool.

**Widget keys.** The key you embed in a customer's page is public by design — it was also authorizing the management API (rewrite the agent's instruction and model, memory blocks, file upload) and returning MCP server config with it. There is now a separate admin key; a migration backfills existing keys. The key's origin allowlist also matches whole hosts now (a prefix match let `victim.com.evil.com` through) and covers the chat surface, not only admin routes.

**Smaller things.** Audit-log output is escaped (it rendered attacker-influenced fields into the page unescaped, and took the client IP from a raw `X-Forwarded-For`). Bearer tokens expire — `TOKEN_TTL_HOURS`, default 24h. Credential comparisons are constant-time. Both agent runtimes default to a loopback bind instead of `0.0.0.0`.

## What you have to do after updating

1. **Run migrations** (`python shared/migrate.py run`) — the widget admin key column is backfilled for existing keys.
2. **The widget admin panel needs the new admin key**, not the embed key: dashboard → widget key → Embed code → Open Admin. Saved `?key=wk_...` links stop working. `WIDGET_LEGACY_ADMIN_KEY=true` re-opens the old behaviour temporarily if you have scripts calling `/widget/api/*` with the public key.
3. **SSO users who were admins only by name matching need the `admin` role** in Users. Basic-auth login always keeps working, so you cannot lock yourself out.

Optional: leave `WIDGET_ORIGIN_STRICT=false` for a day and watch the log for `does not match the allowlist` before turning it on.

Embedded chat widgets, basic-auth/bearer/PAT users, the agent runtime, MCP and triggers are unaffected. Test suite went 450 → 519.

## The takeaway

The reported bug was one handler. The same review found three worse issues, and two of the three were "the page checks permissions, the API behind it does not" — a shape that is easy to get wrong once and then repeat. If you run a dashboard with a JSON API behind it, that is the first thing worth grepping for in your own code.

And: if you ever find something in MATE, mail me. This one was reported well, with a repro that touched nothing of mine, and it made the whole thing better.
