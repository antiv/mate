#!/usr/bin/env python3
"""
Every migration, on a real database: an empty one, and one of the last release with rows.

On SQLite in the ordinary tests. CI runs it on PostgreSQL and MySQL too, with
MATE_MIGRATIONS_URL set to a server on which it may create and drop databases.
"""

import os
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from sqlalchemy import column as sql_column, create_engine, inspect, select, table as sql_table, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.utils import models
from shared.utils.migration_system import MigrationSystem

URL = os.getenv("MATE_MIGRATIONS_URL")
# The last migration of the last release (2.1.0), from which an installation upgrades
RELEASED = 52


class TestMigrationsOnDatabase(unittest.TestCase):

    def setUp(self) -> None:
        if URL:
            server = create_engine(URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
            name = f"mate_test_{uuid.uuid4().hex[:12]}"
            self._execute(server, f"CREATE DATABASE {name}")
            self.addCleanup(self._execute, server, f"DROP DATABASE {name}")
            url = make_url(URL).set(database=name)
        else:
            folder = tempfile.TemporaryDirectory()
            self.addCleanup(folder.cleanup)
            url = make_url("sqlite:///" + os.path.join(folder.name, "mate.db"))
        self.engine = create_engine(url, poolclass=NullPool)
        self.migrations = MigrationSystem(database_client=SimpleNamespace(_engine=self.engine))
        self.files = self.migrations._get_migration_files()

    @staticmethod
    def _execute(engine: Engine, statement: str) -> None:
        with engine.connect() as connection:
            connection.execute(text(statement))

    def _migrate(self, up_to: Optional[int] = None) -> None:
        """Applies the migrations; fails on a statement that failed and was passed over."""
        files = [f for f in self.files if up_to is None or int(f[0]) <= up_to]
        with patch.object(self.migrations, "_get_migration_files", return_value=files), \
                self.assertLogs("shared.utils.migration_system", level="INFO") as logs:
            done = self.migrations.run_migrations()
        failed = [r.getMessage() for r in logs.records if r.levelname in ("WARNING", "ERROR")]
        self.assertEqual(failed, [])
        self.assertTrue(done)
        with self.engine.connect() as connection:
            applied = [row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))]
        self.assertEqual(sorted(applied), sorted(f[0] for f in files))

    def _assert_holds_the_models(self) -> None:
        database = inspect(self.engine)
        missing: List[str] = []
        for table in models.Base.metadata.sorted_tables:
            if not database.has_table(table.name):
                missing.append(table.name)
                continue
            columns = {c["name"] for c in database.get_columns(table.name)}
            missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in columns]
        self.assertEqual(missing, [])
        with Session(self.engine) as session:
            for mapper in models.Base.registry.mappers:
                session.execute(select(mapper.class_).limit(1)).all()

    def test_empty_database(self) -> None:
        self._migrate()
        self._assert_holds_the_models()
        self._migrate()  # again: nothing to do

    def test_database_of_the_last_release_with_rows(self) -> None:
        self._migrate(up_to=RELEASED)
        before = self._fill()
        self._migrate()
        self._assert_holds_the_models()
        self.assertEqual(self._rows(before), before)

    def _fill(self) -> Dict[str, List[Dict[str, Any]]]:
        """Rows in the tables a migration is likely to change. Only the columns this
        database has are written: a later model has more."""
        database = inspect(self.engine)
        with self.engine.begin() as db:
            def add(model: Any, **row: Any) -> None:
                table = model.__table__
                for column in table.columns:
                    if column.default is not None and column.default.is_scalar:
                        row.setdefault(column.name, column.default.arg)
                    elif column.default is not None and column.default.is_callable:
                        row.setdefault(column.name, column.default.arg(None))
                here = {c["name"] for c in database.get_columns(table.name)}
                row = {name: value for name, value in row.items() if name in here}
                plain = sql_table(table.name, *(sql_column(name, table.c[name].type) for name in row))
                db.execute(plain.insert().values(row))

            project = db.execute(text("SELECT MIN(id) FROM projects")).scalar()
            add(models.User, user_id="ana", roles='["user"]')
            add(models.WorkspaceMember, workspace_id=1, user_id="ana")
            add(models.WorkspaceProviderKey, workspace_id=1, provider="openrouter", api_key="sk-kept")
            add(models.AgentConfig, name="kept_root", type="llm", model_name="openrouter/x/y",
                instruction="Čuvaj 🙂", project_id=project, tool_config='{"memory_blocks": true}')
            agent = db.execute(text("SELECT id FROM agents_config WHERE name = 'kept_root'")).scalar()
            add(models.AgentConfigVersion, agent_config_id=agent, version_number=1,
                config_snapshot='{"name": "kept_root"}')
            add(models.AgentTrigger, name="kept", agent_name="kept_root", project_id=project, prompt="p",
                cron_expression="0 7 * * 1")
            add(models.ApprovalRequest, agent_name="kept_root", app_name="kept_root", user_id="ana",
                session_id="s", confirmation_call_id="c", tool_name="t", source="trigger")
            add(models.TokenUsageLog, request_id="r", agent_name="kept_root", model_name="openrouter/x/y",
                prompt_tokens=10, response_tokens=5, cost_usd=0.25, project_id=project, workspace_id=1)
        tables = ("users", "projects", "workspaces", "workspace_members", "workspace_provider_keys",
                  "agents_config", "agent_config_versions", "agent_triggers", "approval_requests",
                  "token_usage_logs")
        return self._rows({t: [dict.fromkeys(c["name"] for c in database.get_columns(t))] for t in tables})

    def _rows(self, like: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
        quote = self.engine.dialect.identifier_preparer.quote
        with self.engine.connect() as db:
            rows = {}
            for table, example in like.items():
                columns = ", ".join(quote(c) for c in example[0])
                found = db.execute(text(f"SELECT {columns} FROM {quote(table)} ORDER BY id"))
                rows[table] = [dict(row._mapping) for row in found]
        self.assertTrue(all(rows.values()), {t: len(r) for t, r in rows.items()})
        return rows


if __name__ == "__main__":
    unittest.main()
