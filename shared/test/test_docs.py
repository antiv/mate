#!/usr/bin/env python3
"""
Tests for the in-dashboard documentation: the service that reads docs/, its
search, and the /dashboard/api/docs routes.

The docs are read from disk and addressed by a path from the query string, so
these pin that a path can only ever name a page that was loaded from docs/, and
that search behaves the way the search box promises: every word must match, a
word may be typed partially, and a title match outranks a body match.
"""

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import docs_routes
from server.dashboard_authz import _is_user_readable, _is_user_writable
from shared.utils import docs_service
from shared.utils.docs_service import (
    DocsService, extract_headings, parse_frontmatter, slugify, tokenize,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GUIDE = """---
title: Triggers
summary: Run an agent on a schedule.
audience: user
order: 20
covers:
  - shared/utils/trigger_runner.py
---

# Triggers

Intro paragraph about schedules.

## Run from a webhook

Send the fire key in a header. Set `SMTP_HOST` for email.

```bash
# not a heading
curl -X POST https://example.com
```

## Run from a webhook

A second section with the same title.
"""

OTHER = """---
title: Memory blocks
summary: Shared notes for agents.
audience: user
order: 10
status: migrated
---

# Memory blocks

Agents read and write blocks. A trigger can write one too.
"""

GENERATED = """---
title: Configuration reference
summary: Every variable.
audience: dev
order: 10
generated: true
---

<!-- AUTO-GENERATED. Do not edit. -->

# Configuration reference

| Variable | Default |
|---|---|
| `SMTP_HOST` | — |
| `WEBHOOK_TIMEOUT` | `30` |
"""


def make_tree(root: Path) -> None:
    (root / "user").mkdir(parents=True)
    (root / "reference").mkdir()
    (root / "user" / "triggers.md").write_text(GUIDE, encoding="utf-8")
    (root / "user" / "memory.md").write_text(OTHER, encoding="utf-8")
    (root / "reference" / "configuration.md").write_text(GENERATED, encoding="utf-8")
    # Outside the served sections: must never be listed or readable.
    (root / "README.md").write_text("# Not a page\n", encoding="utf-8")
    (root.parent / "secret.md").write_text("# Secret\n", encoding="utf-8")


class TestTextHelpers(unittest.TestCase):

    def test_frontmatter_scalars_and_lists(self):
        meta, body = parse_frontmatter(GUIDE)
        self.assertEqual(meta["title"], "Triggers")
        self.assertEqual(meta["order"], "20")
        self.assertEqual(meta["covers"], ["shared/utils/trigger_runner.py"])
        self.assertTrue(body.startswith("# Triggers"))

    def test_page_without_frontmatter_is_all_body(self):
        meta, body = parse_frontmatter("# Plain\n\ntext")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# Plain\n\ntext")

    def test_slug_matches_github(self):
        self.assertEqual(slugify("Where the answer goes"), "where-the-answer-goes")
        self.assertEqual(slugify("Test, pause, change, delete"), "test-pause-change-delete")
        self.assertEqual(slugify("`agent_triggers`"), "agent_triggers")
        self.assertEqual(slugify("Dashboard - Triggers"), "dashboard---triggers")

    def test_headings_skip_code_and_get_unique_anchors(self):
        _, body = parse_frontmatter(GUIDE)
        headings = extract_headings(body)
        self.assertEqual([h["text"] for h in headings],
                         ["Triggers", "Run from a webhook", "Run from a webhook"])
        self.assertEqual([h["anchor"] for h in headings],
                         ["triggers", "run-from-a-webhook", "run-from-a-webhook-1"])

    def test_tokenize_splits_identifiers_folds_and_stems(self):
        self.assertEqual(tokenize("SMTP_HOST"), ["smtp", "host"])
        self.assertEqual(tokenize("Triggers"), ["trigger"])
        self.assertEqual(tokenize("Šta je čvor"), ["sta", "je", "cvor"])


class TestDocsService(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "docs"
        make_tree(self.root)
        self.service = DocsService(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_pages_are_listed_by_section_then_order(self):
        pages = self.service.pages()
        self.assertEqual([p["path"] for p in pages],
                         ["user/memory.md", "user/triggers.md", "reference/configuration.md"])
        self.assertTrue(pages[2]["generated"])
        self.assertEqual([p["status"] for p in pages], ["migrated", "", ""])
        self.assertNotIn("markdown", pages[0])

    def test_page_returns_body_without_frontmatter_or_comments(self):
        page = self.service.page("reference/configuration.md")
        self.assertTrue(page["markdown"].startswith("# Configuration reference"))
        self.assertNotIn("AUTO-GENERATED", page["markdown"])
        self.assertNotIn("title:", page["markdown"])

    def test_path_can_only_name_a_loaded_page(self):
        for path in ("README.md", "../secret.md", "user/../../secret.md", "/etc/passwd",
                     "user/triggers.md/", "user\\triggers.md", "", "user/missing.md"):
            self.assertIsNone(self.service.page(path), path)

    def test_missing_docs_folder_is_empty_not_an_error(self):
        empty = DocsService(Path(self._tmp.name) / "nowhere")
        self.assertEqual(empty.pages(), [])
        self.assertEqual(empty.search("anything"), [])
        self.assertIsNone(empty.page("user/triggers.md"))

    def test_search_requires_every_term(self):
        self.assertTrue(self.service.search("fire key"))
        self.assertEqual(self.service.search("fire nonexistentword"), [])
        self.assertEqual(self.service.search(""), [])
        self.assertEqual(self.service.search("   ,,, "), [])

    def test_search_matches_partial_words_and_plurals(self):
        self.assertEqual(self.service.search("web")[0]["path"], "user/triggers.md")
        self.assertEqual(self.service.search("trigger")[0]["path"], "user/triggers.md")
        self.assertEqual(self.service.search("block")[0]["path"], "user/memory.md")

    def test_title_match_outranks_body_match(self):
        results = self.service.search("trigger")
        self.assertEqual(results[0]["path"], "user/triggers.md")
        self.assertIn("user/memory.md", [r["path"] for r in results[1:]])

    def test_identifier_is_found_and_snippet_shows_it(self):
        results = self.service.search("smtp_host")
        self.assertEqual({r["path"] for r in results},
                         {"user/triggers.md", "reference/configuration.md"})
        for result in results:
            self.assertIn("SMTP_HOST", result["snippet"])

    def test_result_links_to_the_matching_section(self):
        result = self.service.search("second section")[0]
        self.assertEqual((result["path"], result["anchor"]), ("user/triggers.md", "run-from-a-webhook-1"))
        self.assertEqual(result["heading"], "Run from a webhook")

    def test_page_title_counts_for_every_section_of_the_page(self):
        # "trigger" is only in the title; "fire key" only in a later section.
        result = self.service.search("trigger fire key")[0]
        self.assertEqual((result["path"], result["anchor"]), ("user/triggers.md", "run-from-a-webhook"))

    def test_search_can_be_limited_to_a_section(self):
        results = self.service.search("smtp", sections=["reference"])
        self.assertEqual([r["path"] for r in results], ["reference/configuration.md"])

    def test_edits_show_up_without_a_restart(self):
        self.assertEqual(self.service.search("zebra"), [])
        time.sleep(0.01)
        (self.root / "user" / "memory.md").write_text(OTHER + "\nA zebra appears.\n", encoding="utf-8")
        self.assertEqual(self.service.search("zebra")[0]["path"], "user/memory.md")
        (self.root / "user" / "memory.md").unlink()
        self.assertIsNone(self.service.page("user/memory.md"))


class TestShippedDocs(unittest.TestCase):
    """The docs/ tree in the repo must load cleanly, since the dashboard serves it as is."""

    def setUp(self):
        self.service = DocsService(REPO_ROOT / "docs")

    def test_every_page_has_a_title_and_summary(self):
        pages = self.service.pages()
        self.assertTrue(pages, "docs/ has no pages")
        for page in pages:
            self.assertTrue(page["title"] and page["summary"], page["path"])

    def test_anchors_are_unique_within_a_page(self):
        for page in self.service.pages():
            anchors = [h["anchor"] for h in self.service.page(page["path"])["headings"]]
            self.assertEqual(len(anchors), len(set(anchors)), page["path"])

    def test_generator_and_service_read_frontmatter_the_same_way(self):
        spec = importlib.util.spec_from_file_location("gen_docs", REPO_ROOT / "scripts" / "gen_docs.py")
        gen_docs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen_docs)
        for file in sorted((REPO_ROOT / "docs").rglob("*.md")):
            expected, _ = gen_docs.read_frontmatter(file)
            actual, _ = parse_frontmatter(file.read_text(encoding="utf-8"))
            self.assertEqual(actual, expected, str(file))


class TestDocsRoutes(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "docs"
        make_tree(root)
        self._saved = docs_service._service
        docs_service._service = DocsService(root)
        self.app = FastAPI()
        self.app.include_router(docs_routes.router)
        self.app.dependency_overrides[docs_routes.current_user] = lambda: "admin"
        self.client = TestClient(self.app)

    def tearDown(self):
        docs_service._service = self._saved
        self._tmp.cleanup()

    def test_pages(self):
        data = self.client.get("/dashboard/api/docs/pages").json()
        self.assertEqual(data["sections"], ["user", "dev", "reference"])
        self.assertEqual(len(data["pages"]), 3)

    def test_page_and_not_found(self):
        ok = self.client.get("/dashboard/api/docs/page", params={"path": "user/triggers.md"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["headings"][1]["anchor"], "run-from-a-webhook")
        for path in ("../secret.md", "README.md", "/etc/passwd"):
            response = self.client.get("/dashboard/api/docs/page", params={"path": path})
            self.assertEqual(response.status_code, 404, path)

    def test_search_and_bad_section(self):
        data = self.client.get("/dashboard/api/docs/search", params={"q": "fire key"}).json()
        self.assertEqual(data["results"][0]["path"], "user/triggers.md")
        bad = self.client.get("/dashboard/api/docs/search", params={"q": "x", "section": "nope"})
        self.assertEqual(bad.status_code, 400)
        long_query = self.client.get("/dashboard/api/docs/search", params={"q": "a" * 500})
        self.assertEqual(long_query.status_code, 422)

    def test_signed_out_is_refused(self):
        self.app.dependency_overrides[docs_routes.current_user] = lambda: None
        for url in ("/dashboard/api/docs/pages", "/dashboard/api/docs/page?path=user/triggers.md",
                    "/dashboard/api/docs/search?q=fire"):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_routes_are_admin_only(self):
        # DashboardAuthzMiddleware requires admin for /dashboard/api/* unless a path is
        # allowlisted. The docs describe admin features, so they must not be.
        for path in ("/dashboard/api/docs/pages", "/dashboard/api/docs/page", "/dashboard/api/docs/search"):
            self.assertTrue(path.startswith("/dashboard/api/"))
            self.assertFalse(_is_user_readable(path), path)
            self.assertFalse(_is_user_writable(path), path)


if __name__ == "__main__":
    unittest.main()
