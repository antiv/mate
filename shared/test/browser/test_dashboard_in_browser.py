#!/usr/bin/env python3
"""
Every dashboard page in headless Chromium, in both themes, on a desktop and on a phone.

Runs with MATE_BROWSER_TESTS=1 and Chromium installed (playwright install chromium); the
pages load scripts and fonts from CDNs, so it needs the internet. docs/dev/browser-tests.md.
"""

import os
import re
import unittest
from typing import Any, Dict, List

from shared.test.browser.server import PASSWORD, ROOT, USERNAME, Mate
from shared.test.browser.stand_in_model import ANSWER, TITLE

ENABLED = os.getenv("MATE_BROWSER_TESTS") == "1"

# The path of each page; {agent} is the project's root agent.
PAGES = [
    "/dashboard/overview", "/dashboard/users", "/dashboard/workspaces", "/dashboard/members",
    "/dashboard/models", "/dashboard/invite/{token}", "/dashboard/agents", "/dashboard/agents/visual",
    "/dashboard/agents/new", "/dashboard/agents/{agent_name}", "/dashboard/templates",
    "/dashboard/migrations", "/dashboard/usage", "/dashboard/rate-limits", "/dashboard/traces",
    "/dashboard/docs", "/dashboard/audit-logs", "/dashboard/guardrail-logs", "/dashboard/sessions",
    "/dashboard/wizard-leads", "/dashboard/wizard-pricing", "/dashboard/shop-orders",
    "/dashboard/evals", "/dashboard/triggers", "/dashboard/integrations", "/dashboard/workroom",
    "/dashboard/approvals", "/dashboard/alerts", "/dashboard/settings",
]
NOT_A_PAGE = {
    "/dashboard/workroom/canvas",  # the sandboxed frame of the Work Room's canvas
}
SIZES = {"desktop": (1440, 900), "phone": (390, 844)}

# What a person looking at the page would see as broken. Returns sentences.
LOOK = r"""() => {
  const found = [], W = window.innerWidth, H = window.innerHeight;
  const name = e => (e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + ' "' +
    (e.getAttribute('aria-label') || e.innerText || e.name || e.placeholder || '').trim().replace(/\s+/g, ' ').slice(0, 40) + '"');
  if (document.documentElement.scrollWidth > W + 1)
    found.push(`the page is ${document.documentElement.scrollWidth}px wide in a ${W}px window`);
  const help = document.querySelector('#mateHelp [aria-label="Open help"]');
  const helpBox = help && help.getClientRects().length ? help.getBoundingClientRect() : null;
  for (const e of document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary')) {
    if (!e.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || (help && help.parentElement.contains(e))) continue;
    if (r.right <= 0 || r.left >= W) continue;   // a drawer, off screen until it is opened
    let scrolls = false, box = null;
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      const s = getComputedStyle(p), x = s.overflowX;
      if (x === 'auto' || x === 'scroll') { scrolls = true; break; }
      if (x === 'hidden' || x === 'clip') { box = box || p.getBoundingClientRect(); }
      if (s.position === 'fixed') break;
    }
    if (e.closest('.react-flow__viewport')) continue;   // the canvas pans
    if (box && (r.right > box.right + 2 || r.left < box.left - 2)) found.push(`${name(e)} is cut off by its box`);
    else if (!scrolls && (r.right > W + 1 || r.left < -1)) found.push(`${name(e)} is off the window`);
    if (helpBox && r.top < H && r.bottom > 0 && !e.closest('[role=dialog]')) {
      const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
      if (x > helpBox.left && x < helpBox.right && y > helpBox.top && y < helpBox.bottom)
        found.push(`${name(e)} is under the Help button`);
    }
  }
  const nodes = [...document.querySelectorAll('.react-flow__node')].map(e => [e, e.getBoundingClientRect()]);
  nodes.forEach(([a, p], i) => nodes.slice(i + 1).forEach(([b, q]) => {
    if (p.left < q.right - 1 && q.left < p.right - 1 && p.top < q.bottom - 1 && q.top < p.bottom - 1)
      found.push(`${name(a)} overlaps ${name(b)}`);
  }));
  return [...new Set(found)];
}"""
UNDER_HELP = "is under the Help button"
TO_THE_END = """() => { for (const e of [document.scrollingElement, ...document.querySelectorAll('main, main *')])
  if (e.scrollHeight > e.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(e).overflowY)) e.scrollTop = e.scrollHeight;
  window.scrollTo(0, document.documentElement.scrollHeight); }"""


class TestPageList(unittest.TestCase):
    def test_every_page_is_looked_at(self) -> None:
        """Runs without a browser: a new page has to be added to PAGES."""
        with open(os.path.join(ROOT, "shared/utils/dashboard/dashboard_server.py"), encoding="utf-8") as f:
            routes = re.findall(r'app\.get\("(/dashboard/[^"]+)", response_class=HTMLResponse', f.read())
        self.assertEqual(sorted(set(routes) - NOT_A_PAGE), sorted(PAGES))


@unittest.skipUnless(ENABLED, "MATE_BROWSER_TESTS=1 runs the dashboard in a browser")
class TestDashboardInBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from playwright.sync_api import sync_playwright
        cls.mate = Mate()
        cls.addClassCleanup(cls.mate.stop)
        cls.mate.start()
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)
        context = cls.browser.new_context()
        page = context.new_page()
        page.goto(cls.mate.url + "/login")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard/**")
        cls.signed_in = context.storage_state()
        context.close()

    def _open(self, size: str, theme: str) -> Any:
        width, height = SIZES[size]
        context = self.browser.new_context(viewport={"width": width, "height": height},
                                           storage_state=self.signed_in)
        self.addCleanup(context.close)
        context.add_init_script(f"localStorage.setItem('theme', '{theme}');"
                                f"localStorage.setItem('agentDashboardSelectedProjectId', '{self.mate.project_id}')")
        page = context.new_page()
        self.problems: List[str] = []
        page.on("console", lambda m: m.type == "error" and self.problems.append(f"console: {m.text[:300]}"))
        page.on("pageerror", lambda e: self.problems.append(f"script error: {str(e)[:300]}"))
        page.on("response", lambda r: r.status >= 400 and r.url.startswith(self.mate.url)
                and self.problems.append(f"HTTP {r.status}: {r.url[len(self.mate.url):]}"))
        return page

    def _url(self, path: str) -> str:
        return self.mate.url + path.replace("{agent_name}", self.mate.agent).replace("{token}", "unknown")

    def _look_at(self, size: str, theme: str) -> None:
        page = self._open(size, theme)
        broken: Dict[str, List[str]] = {}
        for path in PAGES:
            self.problems.clear()
            page.goto(self._url(path), wait_until="load")
            page.wait_for_timeout(1500)
            # A control may pass under the Help button, if scrolling to the end clears it
            found = [p for p in page.evaluate(LOOK) if not p.endswith(UNDER_HELP)]
            page.evaluate(TO_THE_END)
            page.wait_for_timeout(200)
            found += [p for p in page.evaluate(LOOK) if p.endswith(UNDER_HELP)]
            problems = list(self.problems) + found
            if problems:
                broken[path] = problems
        if broken:
            self.fail(f"{size}, {theme}:\n" + "\n".join(
                f"{path}\n    " + "\n    ".join(found) for path, found in broken.items()))

    def test_desktop_light(self) -> None:
        self._look_at("desktop", "light")

    def test_desktop_dark(self) -> None:
        self._look_at("desktop", "dark")

    def test_phone_light(self) -> None:
        self._look_at("phone", "light")

    def test_phone_dark(self) -> None:
        self._look_at("phone", "dark")

    def test_looking_finds_what_is_broken(self) -> None:
        page = self._open("desktop", "light")
        page.goto(self.mate.url + "/dashboard/settings")
        page.locator('#mateHelp [aria-label="Open help"]').wait_for(timeout=15000)
        self.assertEqual([p for p in page.evaluate(LOOK) if not p.endswith(UNDER_HELP)], [])
        page.evaluate("""() => {
          const help = document.querySelector('#mateHelp [aria-label="Open help"]').getBoundingClientRect();
          const add = html => document.body.insertAdjacentHTML('beforeend', html);
          add(`<button style="position:fixed;left:${help.left + 5}px;top:${help.top + 5}px;width:20px;height:20px">covered</button>`);
          add('<button style="position:fixed;left:1400px;width:200px">outside</button>');
          add('<div style="position:fixed;left:300px;top:300px;width:50px;height:40px;overflow:hidden"><button style="width:200px">cut</button></div>');
        }""")
        self.assertEqual(sorted(p for p in page.evaluate(LOOK) if "covered" in p or "outside" in p or "cut" in p), [
            'button "covered" is under the Help button', 'button "cut" is cut off by its box',
            'button "outside" is off the window'])

    def test_visual_builder(self) -> None:
        page = self._open("desktop", "light")
        page.goto(self.mate.url + f"/dashboard/agents/visual?project_id={self.mate.project_id}")
        page.wait_for_selector(".react-flow__node", timeout=15000)
        page.wait_for_timeout(1500)
        self.assertEqual(self.problems + page.evaluate(LOOK), [])
        page.locator(".react-flow__node", has_text=self.mate.agent).first.click()
        page.wait_for_timeout(1000)
        self.assertEqual(self.problems + page.evaluate(LOOK), [])

    def test_conversation_is_answered_and_named(self) -> None:
        page = self._open("desktop", "light")
        page.goto(self.mate.url + f"/dashboard/workroom?agent={self.mate.agent}")
        page.fill("#widgetInput", "Can you help me?")
        page.keyboard.press("Enter")
        page.get_by_text(ANSWER).first.wait_for(timeout=60000)
        page.get_by_text(TITLE).first.wait_for(timeout=30000)
        self.assertEqual(self.problems, [])


if __name__ == "__main__":
    unittest.main()
