---
title: Documentation system
summary: How the docs are generated, checked for drift, served in the dashboard and searched.
audience: dev
order: 900
covers:
  - scripts/gen_docs.py
  - shared/utils/docs_service.py
  - server/docs_routes.py
  - templates/dashboard/docs.html
  - static/js/docs-page.js
---

# Documentation system

The documentation is Markdown in the repo's `docs/` folder. The dashboard's
**Documentation** page serves that folder as it is on disk, so an installation always
shows the docs that belong to the code it runs.

```
docs/
  user/        Guides for people using the dashboard. Task-oriented, no code.
  dev/         Guides for people changing or integrating with MATE.
  reference/   Generated from the code. Never edited by hand.
```

## Reference pages are generated

`python scripts/gen_docs.py` rebuilds everything under `reference/` by static
analysis. It imports nothing from the application and needs no dependencies.

| Page | Built from |
|---|---|
| `configuration.md` | every `os.getenv` / `os.environ` read, joined with the comments in `.env.example` |
| `api.md` | every route decorator, with the first sentence of its docstring |
| `tools.md` | `ToolFactory._tool_creators` |
| `database.md` | the SQLAlchemy models and the migration folders |

The output has no timestamps and no line numbers, so it changes only when the code
it describes changes. CI runs `python scripts/gen_docs.py --check` and fails when the
committed pages differ from what the code produces. To improve a reference page,
improve its source: a route's docstring, a column's comment, a variable's line in
`.env.example`.

## Guides declare what they describe

Every page under `user/` and `dev/` starts with frontmatter:

```yaml
---
title: Triggers
summary: Run an agent on a schedule or from a webhook and send the result somewhere.
audience: user            # must match the folder: user or dev
order: 60                 # position in the sidebar, lowest first
covers:
  - shared/utils/trigger_runner.py
  - templates/dashboard/modals/trigger_modal.html
  - shared/utils/dashboard/dashboard_server.py::*trigger*
---
```

`title` and `summary` are what the sidebar and search results show. An optional
`status: migrated` marks a page that was moved from the earlier `documents/` folder
without being re-checked against the code; the dashboard shows a notice on it.
Remove the line once the page has been verified. `python scripts/gen_docs.py --check`
prints how many such pages remain. `covers` lists
the code the page describes. An entry is a path or glob (`**` spans folders). Add
`::pattern` to narrow it to functions and classes whose names match, which matters
for large files: `dashboard_server.py::*trigger*` reacts to a change in
`create_trigger` but not to a change in the usage charts.

`--check` also validates guides: required keys present, every `covers` entry still
matches something, every relative link resolves. And it fails when a migration
version is missing from one of the `sqlite/`, `postgresql/` and `mysql/` folders.

## Keeping guides current

```bash
python scripts/gen_docs.py drift --base origin/main
```

lists every guide whose covered code changed on this branch while the guide itself
did not. Symbol-level entries compare the parsed code, so reformatting and comment
edits do not count. It is a prompt to look, not proof the guide is wrong: update the
guide in the same pull request, or state in the PR that it still holds. CI reports
drift as warnings; `--strict` makes it fail the build.

```bash
python scripts/gen_docs.py coverage
```

lists the source files no guide covers yet.

## How the dashboard serves the docs

| Piece | Where | Role |
|---|---|---|
| `DocsService` | `shared/utils/docs_service.py` | Reads `docs/`, lists pages, returns one page, searches. |
| Routes | `server/docs_routes.py` | `GET /dashboard/api/docs/pages`, `/page?path=`, `/search?q=`. |
| Page | `templates/dashboard/docs.html`, `static/js/docs-page.js` | Tabs, navigation, rendering, search box. |

**Loading.** The service reads every `*.md` under the three section folders and keeps
them in memory. Each request compares file sizes and modification times and reloads
when anything changed, so an edited page appears on the next request without a
restart. A missing `docs/` folder yields an empty list, not an error.

**Paths.** The `path` parameter is used only as a key into the pages already loaded.
It never reaches the filesystem, so it cannot name a file outside `docs/`.

**Access.** The routes are under `/dashboard/api/`, which `DashboardAuthzMiddleware`
restricts to admins, like the Documentation page itself. Opening the user guides to
non-admin users would mean adding the routes to its allowlist and filtering by
section in the handlers.

**Rendering.** The browser renders Markdown with markdown-it, loaded from jsDelivr at
a pinned version, with raw HTML disabled: HTML in a page is shown as text, not
executed. If the script cannot be loaded, the page shows the Markdown source instead.
Heading anchors are computed on the server (GitHub style, so links written for GitHub
work) and applied to the rendered headings in order.

**Addresses.** The address bar always names what is shown:
`/dashboard/docs?page=user/triggers.md#where-the-answer-goes`, or `?tab=api` for the
API explorer. Relative links between pages (`../dev/trigger-engine.md#secrets`) are
followed inside the page.

**Packaging.** The Dockerfile copies `docs/` into the image. `.dockerignore` excludes
root-level `*.md` and explicitly keeps `docs/**/*.md`.

## How search works

Search runs on the server, over all pages, on every query. There is no index to
build or keep in sync, and no external service.

- Pages are cut at their headings (levels 1 to 3). Each part is a separate result
  that links to its heading.
- Text is lowercased, stripped of diacritics, split on anything that is not a letter
  or digit (so `SMTP_HOST` is `smtp` and `host`), and a plural `s` is dropped.
- **Every** term of the query must match a part. A term matches a word it equals or
  starts, so results appear while typing.
- A match in the page title scores 12 for the page's opening part and 4 for every
  later section of that page, so a query can combine the page's topic with a word
  from one section. A match in the section heading scores 8, in the body 1 per
  occurrence up to 5. A prefix-only match counts 0.6 of that. The exact phrase adds
  4. Generated reference pages are scaled by 0.85 so that a guide explaining a topic
  ranks above the table that lists it.
- The snippet is taken around the exact phrase if it occurs, otherwise around the
  place where most terms occur together.

Opening a result highlights the terms in the page and scrolls to the first match in
that section.

## Writing a guide

- One page per thing a reader wants to do or understand. Split by audience: the
  user page says what to click and what will happen; the dev page says how it works
  and where the code is.
- State what the code does today. If behaviour is surprising, say so plainly rather
  than describing the intended behaviour.
- Link to reference pages for exhaustive lists (variables, routes, columns) rather
  than copying them: copies go stale, the reference cannot.
