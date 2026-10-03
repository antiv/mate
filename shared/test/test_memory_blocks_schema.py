"""
The memory_blocks schema from the ORM matches V002, and DatabaseClient does not
create ORM tables on top of a schema whose migrations failed (issue #145).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import UniqueConstraint, create_engine, inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.utils.database_client import DatabaseClient
from shared.utils.models import Base, MemoryBlock


class TestMemoryBlockUniqueConstraint(unittest.TestCase):

    def test_model_declares_project_label_unique(self):
        uniques = [tuple(c.name for c in con.columns)
                   for con in MemoryBlock.__table__.constraints
                   if isinstance(con, UniqueConstraint)]
        self.assertIn(("project_id", "label"), uniques)

    def test_create_all_builds_the_constraint(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        uniques = inspect(engine).get_unique_constraints("memory_blocks")
        self.assertIn(["project_id", "label"], [u["column_names"] for u in uniques])


class TestCreateTablesOnlyAfterMigrations(unittest.TestCase):

    def _init(self, migrations_ok: bool) -> MagicMock:
        engine = MagicMock()
        with patch.dict(os.environ, {"DB_TYPE": "postgresql", "DB_NAME": "m",
                                     "DB_USER": "u", "DB_PASSWORD": "p"}), \
                patch("shared.utils.database_client.create_engine", return_value=engine), \
                patch.object(DatabaseClient, "_run_migrations", return_value=migrations_ok), \
                patch.object(DatabaseClient, "_auto_create_tables") as create_tables, \
                patch.object(DatabaseClient, "_run_audit_retention"):
            DatabaseClient()
        return create_tables

    def test_creates_tables_when_migrations_pass(self):
        self._init(migrations_ok=True).assert_called_once()

    def test_skips_tables_when_migrations_fail(self):
        self._init(migrations_ok=False).assert_not_called()


if __name__ == "__main__":
    unittest.main()
