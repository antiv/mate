## What and why

<!-- What does this change do, and what problem does it solve? Link the issue: "Closes #123". -->

## How it was tested

<!-- Commands you ran and what you checked by hand. -->

## Checklist

- [ ] Tests added or updated in `shared/test/`, and `python -m unittest discover -s shared/test -t . -p "test_*.py"` passes
- [ ] Schema changes have a migration for all three dialects (`shared/sql/migrations/{postgresql,mysql,sqlite}/`)
- [ ] Changed a route, env var, model or the tool registry: ran `python scripts/gen_docs.py` and committed `docs/reference/`
- [ ] Ran `python scripts/gen_docs.py drift --base origin/main` and updated the guides it lists (or say below why they still hold)
- [ ] New user-facing feature: a guide in `docs/user/` (and `docs/dev/` if it has internals worth knowing)
- [ ] No API keys, tokens or credentials in code, logs or tests
