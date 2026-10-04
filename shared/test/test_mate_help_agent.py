#!/usr/bin/env python3
"""
The built-in help agent (mate_help) and the docs tool it answers from.

The docs tool searches and reads docs/. Admins get every section; everyone else
only the user guides, and links to the Documentation page, which only admins can
open, go to admins only. Migration V036 creates the agent without a model, so it
uses the server's default until an admin picks one, and never overwrites an
existing agent of the same name, so that choice survives.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.tools.docs_tools import create_docs_tools_from_config
from shared.utils.tools.tool_factory import ToolFactory


def _context(user_id="someone"):
    return SimpleNamespace(_invocation_context=SimpleNamespace(user_id=user_id))


class DocsToolsTestCase(unittest.TestCase):

    def setUp(self):
        self.tools = {t.__name__: t for t in create_docs_tools_from_config({"name": "mate_help"})}

    def as_admin(self, admin=True):
        patcher = patch("shared.utils.tools.docs_tools._is_admin_user", return_value=admin)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestSearchDocs(DocsToolsTestCase):

    def test_admin_finds_user_guides_with_links(self):
        self.as_admin()
        result = self.tools["search_docs"]("webhook signature")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["results"])
        first = result["results"][0]
        self.assertEqual(set(first), {"title", "heading", "snippet", "path", "link"})
        self.assertTrue(first["link"].startswith(f"/dashboard/docs?page={first['path']}"))

    def test_admin_also_searches_developer_guides(self):
        self.as_admin()
        paths = {r["path"] for r in self.tools["search_docs"]("APScheduler")["results"]}
        self.assertTrue(any(p.startswith("dev/") for p in paths), paths)

    def test_others_get_only_user_guides_and_no_links(self):
        self.as_admin(False)
        results = self.tools["search_docs"]("trigger")["results"]
        self.assertTrue(results)
        self.assertTrue(all(r["path"].startswith("user/") for r in results))
        self.assertTrue(all("link" not in r for r in results))
        self.assertEqual(self.tools["search_docs"]("APScheduler")["results"], [])

    def test_no_match_is_an_empty_list_not_an_error(self):
        self.as_admin()
        result = self.tools["search_docs"]("zzqqxx nonexistentword")
        self.assertEqual(result, {"status": "success", "query": "zzqqxx nonexistentword", "results": []})

    def test_an_unknown_user_is_not_an_admin(self):
        # get_user_roles answers ['user'] for an id it does not know.
        with patch("shared.utils.user_service.get_user_service") as service:
            service.return_value.get_user_roles.return_value = ["user"]
            results = self.tools["search_docs"]("trigger", tool_context=_context("stranger"))["results"]
        self.assertTrue(all(r["path"].startswith("user/") for r in results))


class TestReadDocPage(DocsToolsTestCase):

    def test_reads_a_user_guide(self):
        self.as_admin(False)
        result = self.tools["read_doc_page"]("user/triggers.md")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["title"], "Triggers")
        self.assertIn("Run on a schedule", result["markdown"])
        self.assertNotIn("link", result)

    def test_admin_gets_a_link(self):
        self.as_admin()
        self.assertEqual(self.tools["read_doc_page"]("user/triggers.md")["link"],
                         "/dashboard/docs?page=user/triggers.md")

    def test_others_cannot_read_developer_guides(self):
        self.as_admin(False)
        result = self.tools["read_doc_page"]("dev/trigger-engine.md")
        self.assertEqual(result["status"], "error")
        self.assertIn("search_docs", result["error_message"])

    def test_an_unknown_or_hostile_path_is_an_error(self):
        self.as_admin()
        for path in ("user/nope.md", "../CLAUDE.md", "", None):
            with self.subTest(path=path):
                self.assertEqual(self.tools["read_doc_page"](path)["status"], "error")

    def test_a_long_page_is_truncated(self):
        self.as_admin()
        with patch("shared.utils.tools.docs_tools.MAX_PAGE_CHARS", 100):
            markdown = self.tools["read_doc_page"]("user/triggers.md")["markdown"]
        self.assertTrue(markdown.endswith("[Page truncated]"))


class TestRegistry(unittest.TestCase):

    def test_the_docs_key_enables_both_tools(self):
        factory = ToolFactory()
        self.assertIn("docs", factory._tool_creators)
        names = {t.__name__ for t in factory._create_docs_tools({"name": "x"})}
        self.assertEqual(names, {"search_docs", "read_doc_page"})


class TestMigration(unittest.TestCase):
    """V036 on a real SQLite database, through the same path as server startup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "mate.db")
        patcher = patch.dict(os.environ, {"DB_TYPE": "sqlite", "DB_PATH": self.db_path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def migrate(self):
        from shared.utils.migration_system import MigrationSystem
        self.assertTrue(MigrationSystem().run_migrations())

    def query(self, sql, *args):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def test_creates_the_agent_in_its_own_project(self):
        self.migrate()
        rows = self.query(
            "SELECT a.model_name, a.allowed_for_roles, a.tool_config, a.parent_agents, "
            "a.disabled, a.hardcoded, a.instruction, p.name "
            "FROM agents_config a JOIN projects p ON p.id = a.project_id WHERE a.name = 'mate_help'")
        self.assertEqual(len(rows), 1)
        model, roles, tools, parents, disabled, hardcoded, instruction, project = rows[0]
        self.assertIsNone(model)  # the server's default model until an admin picks one
        self.assertEqual(json.loads(roles), ["admin", "user"])
        self.assertEqual(json.loads(tools), {"docs": True})
        self.assertEqual(json.loads(parents), [])
        self.assertEqual((disabled, hardcoded), (0, 0))
        self.assertIn("search_docs", instruction)
        self.assertIn("MATE's documentation", instruction)  # the doubled quote survived
        self.assertEqual(project, "MATE Help")

    def test_an_existing_agent_and_its_model_are_left_alone(self):
        self.migrate()
        # An admin picked a model and edited the instruction; then V036 runs again.
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE agents_config SET model_name = 'openai/gpt-4o-mini', "
                     "instruction = 'mine' WHERE name = 'mate_help'")
        conn.execute("DELETE FROM schema_migrations WHERE version = '036'")
        conn.commit()
        conn.close()

        self.migrate()
        self.assertEqual(
            self.query("SELECT model_name, instruction FROM agents_config WHERE name = 'mate_help'"),
            [("openai/gpt-4o-mini", "mine")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM projects WHERE name = 'MATE Help'"), [(1,)])


class TestMigrationOnCreateAllSchema(TestMigration):
    """V036 on tables made by SQLAlchemy's create_all, which have no database-side
    defaults. On PostgreSQL the insert failed on projects.created_at; on SQLite,
    INSERT OR IGNORE skipped the rows silently and no agent was created."""

    def migrate(self):
        from sqlalchemy import create_engine
        from shared.utils.migration_system import MigrationSystem
        from shared.utils.models import Base
        engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()
        # V001 and V003 fail on such a schema for reasons of their own, so the run as
        # a whole reports failure; what matters here is that V036 applied.
        MigrationSystem().run_migrations()
        self.assertEqual(self.query("SELECT version FROM schema_migrations WHERE version = '036'"),
                         [("036",)])


if __name__ == "__main__":
    unittest.main()
