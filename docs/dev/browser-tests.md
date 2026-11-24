---
title: Browser tests
summary: The test that opens every dashboard page in headless Chromium, what it counts as broken, and how to run it.
audience: dev
order: 76
covers:
  - shared/test/browser/*.py
  - .github/workflows/ci.yml
---

# Browser tests

`shared/test/browser/` opens the dashboard in headless Chromium. CI runs it in the job
`browser`, on every pull request.

## Run it

```bash
playwright install chromium   # once; downloads the browser
MATE_BROWSER_TESTS=1 python -m unittest discover -s shared/test -t . -p "test_dashboard_in_browser.py" -v
```

It takes about four minutes. Without `MATE_BROWSER_TESTS=1` only `TestPageList` runs. The
pages load scripts and fonts from CDNs, so it needs the internet.

It doesn't touch your `.env`, database or running server. `server.py` copies the
working tree to a temporary folder, uncommitted changes too, and starts there, on free
ports:

- the dashboard, with `CSP_MODE=enforce` and an empty SQLite database;
- the agent server, on ADK;
- a stand-in for OpenRouter (`stand_in_model.py`), which always gives the same answer.

It then imports a template into a project with a long name, and adds a trigger and 60
usage rows. `launch.py` starts them, not `auth_server.py`, whose shutdown stops every
process on the machine that has `adk_main.py` in its command line.

## What it checks

Every page in `PAGES`, light and dark, at 1440×900 and 390×844:

- no console error, script error or CSP violation, and no answer of 400 or more from MATE;
- the page isn't wider than the window;
- no control is off the window, or cut off by a box that doesn't scroll;
- no control is under the Help button once the page is scrolled to its end.

Also: the Visual Builder's boxes don't overlap, and a Work Room conversation is answered
and gets a name.

`LOOK` is the script that finds layout problems; `test_looking_finds_what_is_broken`
breaks a page and checks that it says so. It doesn't see what only looks wrong: colours,
text over text, spacing.

## A new page

`TestPageList` fails in the ordinary tests when a dashboard page is not in `PAGES`. Add
its path there.
