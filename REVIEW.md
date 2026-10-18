# Review rules

What a reviewer, human or agent, checks on every pull request to MATE, on top of
correctness. Each rule comes from `CLAUDE.md`, `AGENTS.md` or `CONTRIBUTING.md`;
a finding that cites one of these rules is blocking.

## Blocking

- **Tests.** New behaviour has tests in `shared/test/` covering success and
  failure; external APIs are mocked. CI must be green.
- **Migrations.** A schema change ships a migration for PostgreSQL, MySQL and
  SQLite, and the ORM model in `shared/utils/models.py` matches it.
- **Generated reference.** A changed route, env var, model or tool registry
  entry comes with a regenerated `docs/reference/` (`gen_docs.py --check` fails
  otherwise). `docs/reference/` is never edited by hand.
- **Guides.** Guides listed by `gen_docs.py drift` are updated in the same PR,
  or the PR description says why they still hold. A new feature has a user guide.
- **Authorization.** A new dashboard or API route checks the caller (RBAC,
  project scope); a new agent sets `allowed_for_roles` and `project_id`.
- **Secrets.** No credentials in code, tests, fixtures or logs. Stored endpoint
  keys never reach the browser.
- **Database sessions** are closed in `finally` blocks.
- **Tools** are built through `ToolFactory`.
- **Insecure defaults.** Nothing weakens the production startup checks
  (`MATE_ENV=production`), CSP, widget origin checks, the browser's
  private-network block or the `code_executor`-on-widget refusal without a new
  opt-in env var.

## Non-blocking

- PEP 8, type hints on signatures, f-strings, `logger` instead of `print()`.
- Changes outside the PR's stated purpose: ask for them to be split out.
