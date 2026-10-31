#!/usr/bin/env python3
"""
Workspaces, phase 1 of 2.0 (V046, #277): the schema and the default workspace, with
no change in behaviour.

- The migration puts every existing row in workspace 1, on a database that already
  holds data.
- Every user is a member of it, with a role derived from users.roles, and stays so
  as users are added and their roles change.
- New log rows carry their workspace, and older ones get it from their project.
- The inference span names the workspace.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import log_projects, workspaces
from shared.utils.models import (
    AgentConfig, AuditLog, Base, GuardrailLog, Project, TokenUsageLog, User, Workspace,
    WorkspaceMember,
)

LOG_TABLES = ("token_usage_logs", "guardrail_logs", "agent_responses", "audit_logs")
DEFAULTED_TABLES = ("projects", "test_cases", "rate_limit_config", "alert_rules",
                    "personal_access_tokens")


class TestMigration(unittest.TestCase):
    """V046 on SQLite, applied to a database migrated to V045 that holds data."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db_path = os.path.join(tmp.name, "mate.db")
        patcher = patch.dict(os.environ, {"DB_TYPE": "sqlite", "DB_PATH": self.db_path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def migrate(self, up_to=None):
        from shared.utils.migration_system import MigrationSystem
        system = MigrationSystem()
        files = system._get_migration_files()
        if up_to:
            files = [f for f in files if f[0] <= up_to]
        with patch.object(system, "_get_migration_files", return_value=files):
            self.assertTrue(system.run_migrations())

    def sql(self, statement, *args):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(statement, args).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def test_existing_rows_land_in_the_default_workspace(self):
        self.migrate(up_to="045")
        self.assertEqual(self.sql("SELECT MAX(version) FROM schema_migrations"), [("045",)])
        self.sql("INSERT INTO users (user_id, roles) VALUES ('ana', '[\"user\"]')")
        self.sql("INSERT INTO personal_access_tokens (token_hash, token_prefix, name, user_id) "
                 "VALUES ('h', 'mate_pat_x', 't', 'ana')")
        self.sql("INSERT INTO rate_limit_config (scope, scope_id) VALUES ('user', 'ana')")
        self.sql("INSERT INTO token_usage_logs (request_id, agent_name, project_id) "
                 "VALUES ('r1', 'mate_help', 1)")

        self.migrate()

        self.assertEqual(self.sql("SELECT id, name, slug, allow_platform_keys FROM workspaces"),
                         [(1, "Default", "default", 1)])
        for table in DEFAULTED_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self.sql(f"SELECT COUNT(*) FROM {table} WHERE workspace_id IS NOT 1"),
                                 [(0,)])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM personal_access_tokens"), [(1,)])
        # Log rows are left to the backfill, so a large table is not locked
        self.assertEqual(self.sql("SELECT workspace_id FROM token_usage_logs"), [(None,)])
        for table in LOG_TABLES:
            with self.subTest(table=table):
                columns = [row[1] for row in self.sql(f"PRAGMA table_info({table})")]
                self.assertIn("workspace_id", columns)

    def test_new_rows_default_to_it_too(self):
        self.migrate()
        self.sql("INSERT INTO projects (name, created_at, updated_at) "
                 "VALUES ('New', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        self.assertEqual(self.sql("SELECT workspace_id FROM projects WHERE name = 'New'"), [(1,)])

    def test_members_are_unique_per_workspace(self):
        self.migrate()
        self.sql("INSERT INTO users (user_id, roles) VALUES ('ana', '[\"user\"]')")
        self.sql("INSERT INTO workspace_members (workspace_id, user_id) VALUES (1, 'ana')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("INSERT INTO workspace_members (workspace_id, user_id) VALUES (1, 'ana')")

    def test_a_create_all_schema_gets_the_default_workspace(self):
        engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()
        self.assertEqual(self.sql("SELECT id, slug FROM workspaces"), [(1, "default")])
        # and the migrations still apply over it
        self.migrate()
        self.assertEqual(self.sql("SELECT COUNT(*) FROM workspaces"), [(1,)])


class _Db(unittest.TestCase):

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.Session = sessionmaker(bind=engine, expire_on_commit=False)
        self.db = MagicMock()
        self.db.get_session.side_effect = lambda: self.Session()
        self.db.is_connected.return_value = True

    def members(self):
        session = self.Session()
        try:
            return {m.user_id: (m.workspace_id, m.role, json.loads(m.agent_roles))
                    for m in session.query(WorkspaceMember)}
        finally:
            session.close()


class TestMembers(_Db):

    def _add_users_without_members(self, **roles):
        """Users as a 1.x installation has them: no memberships yet."""
        session = self.Session()
        session.add_all([User(user_id=user_id, roles=json.dumps(r)) for user_id, r in roles.items()])
        session.commit()
        session.query(WorkspaceMember).delete()
        session.commit()
        session.close()

    def test_roles_map_to_owner_and_member(self):
        self.assertEqual(workspaces.membership_from_roles(["admin", "user"]), ("owner", ["user"]))
        self.assertEqual(workspaces.membership_from_roles(["user", "developer"]),
                         ("member", ["user", "developer"]))
        self.assertIsNone(workspaces.membership_from_roles(["widget"]))
        self.assertIsNone(workspaces.membership_from_roles(["pending"]))

    def test_the_startup_sync_adds_every_user(self):
        self._add_users_without_members(boss=["admin", "user"], ana=["user", "developer"])
        session = self.Session()
        self.assertEqual(workspaces.sync_default_members(session), {"added": 2, "updated": 0, "removed": 0})
        session.close()
        self.assertEqual(self.members(), {"boss": (1, "owner", ["user"]),
                                          "ana": (1, "member", ["user", "developer"])})
        session = self.Session()
        self.assertEqual(workspaces.sync_default_members(session), {"added": 0, "updated": 0, "removed": 0})
        session.close()

    def test_the_startup_sync_corrects_a_member(self):
        self._add_users_without_members(ana=["user"])
        session = self.Session()
        session.add(WorkspaceMember(workspace_id=1, user_id="ana", role="owner", agent_roles="[]"))
        session.commit()
        self.assertEqual(workspaces.sync_default_members(session), {"added": 0, "updated": 1, "removed": 0})
        session.close()
        self.assertEqual(self.members(), {"ana": (1, "member", ["user"])})

    def test_a_new_user_becomes_a_member(self):
        from shared.utils.user_service import UserService
        with patch("shared.utils.user_service.get_database_client", return_value=self.db):
            UserService().get_or_create_user("ana")
        self.assertEqual(self.members(), {"ana": (1, "member", ["user"])})

    def test_a_role_change_follows(self):
        from shared.utils.user_service import UserService
        with patch("shared.utils.user_service.get_database_client", return_value=self.db):
            service = UserService()
            service.get_or_create_user("ana")
            service.update_user_roles("ana", ["admin", "user"])
        self.assertEqual(self.members(), {"ana": (1, "owner", ["user"])})

    def test_widget_visitors_and_pending_users_are_no_members(self):
        from shared.utils.user_service import UserService
        with patch("shared.utils.user_service.get_database_client", return_value=self.db):
            UserService().get_or_create_user("widget_3_visitor")
        session = self.Session()
        session.add(User(user_id="sso@corp.com", roles='["pending"]'))
        session.commit()
        session.close()
        self.assertEqual(self.members(), {})

    def test_approving_a_pending_user_adds_the_member_and_the_reverse_removes_it(self):
        session = self.Session()
        user = User(user_id="sso@corp.com", roles='["pending"]')
        session.add(user)
        session.commit()
        user.set_roles(["user"])
        session.commit()
        self.assertEqual(self.members(), {"sso@corp.com": (1, "member", ["user"])})
        user.set_roles(["pending"])
        session.commit()
        session.close()
        self.assertEqual(self.members(), {})

    def test_the_startup_sync_removes_a_widget_visitor(self):
        # A membership written before visitors were left out
        self._add_users_without_members(widget_3_visitor=["widget"], ana=["user"])
        session = self.Session()
        session.add(WorkspaceMember(workspace_id=1, user_id="widget_3_visitor", role="member",
                                    agent_roles='["widget"]'))
        session.commit()
        self.assertEqual(workspaces.sync_default_members(session),
                         {"added": 1, "updated": 0, "removed": 1})
        session.close()
        self.assertEqual(self.members(), {"ana": (1, "member", ["user"])})

    def test_other_changes_leave_the_member_alone(self):
        session = self.Session()
        session.add(User(user_id="ana", roles='["user"]'))
        session.commit()
        session.query(WorkspaceMember).update({"invited_by": "kept"})
        session.commit()
        user = session.query(User).filter_by(user_id="ana").one()
        user.profile_data = "likes tea"
        session.commit()
        self.assertEqual(session.query(WorkspaceMember.invited_by).scalar(), "kept")
        session.close()

    def test_deleting_a_user_deletes_the_member(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        from sqlalchemy import event as sa_event
        sa_event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        session.add(User(user_id="ana", roles='["user"]'))
        session.commit()
        session.query(User).filter_by(user_id="ana").delete()
        session.commit()
        self.assertEqual(session.query(WorkspaceMember).count(), 0)
        session.close()


class TestLogRows(_Db):

    def setUp(self):
        super().setUp()
        for target in ("shared.utils.log_projects.get_database_client",
                       "shared.utils.token_usage_service.get_database_client",
                       "shared.utils.audit_service.get_database_client"):
            p = patch(target, return_value=self.db)
            p.start()
            self.addCleanup(p.stop)
        session = self.Session()
        session.add_all([Workspace(id=2, name="Other", slug="other"),
                         Project(id=1, name="Shop"), Project(id=2, name="Elsewhere", workspace_id=2),
                         AgentConfig(name="seller", type="llm", project_id=1),
                         AgentConfig(name="away", type="llm", project_id=2)])
        session.commit()
        session.close()

    def _all(self, model):
        session = self.Session()
        try:
            return session.query(model).order_by(model.id).all()
        finally:
            session.close()

    def test_token_usage_carries_the_workspace(self):
        from shared.utils.token_usage_service import TokenUsageService
        with patch("shared.utils.model_pricing.cost_usd", return_value=None):
            TokenUsageService().insert_token_usage({"agent_name": "away", "model_name": "m"})
            TokenUsageService().insert_token_usage({"agent_name": "gone", "model_name": "m"})
        self.assertEqual([(r.project_id, r.workspace_id) for r in self._all(TokenUsageLog)],
                         [(2, 2), (None, None)])

    def test_audit_entries_carry_the_workspace_of_their_project(self):
        from shared.utils import audit_service
        audit_service.log("boss", "update", "agent", resource_id="away")
        audit_service.log("boss", "login", "auth")
        self.assertEqual([(e.project_id, e.workspace_id) for e in self._all(AuditLog)],
                         [(2, 2), (None, None)])

    def test_the_backfill_fills_older_rows_from_their_project(self):
        session = self.Session()
        session.add_all([TokenUsageLog(request_id=f"r{i}", project_id=p)
                         for i, p in enumerate([1, 2, None, 99, 1])])
        session.add(GuardrailLog(request_id="g", guardrail_type="pii", phase="input",
                                 action_taken="block", project_id=2, workspace_id=1))
        session.commit()
        session.close()

        updated = log_projects.backfill_workspaces(batch_size=2)

        # No project, or a project that is gone: no workspace
        self.assertEqual([r.workspace_id for r in self._all(TokenUsageLog)], [1, 2, None, None, 1])
        # A row that has one keeps it
        self.assertEqual(self._all(GuardrailLog)[0].workspace_id, 1)
        self.assertEqual(updated["token_usage_logs"], 3)

    def test_the_workspace_backfill_waits_for_the_project_one(self):
        stored = {}
        with patch("shared.utils.system_settings.get_setting", side_effect=stored.get), \
                patch("shared.utils.system_settings.set_setting",
                      side_effect=lambda key, value, actor: stored.update({key: value})), \
                patch.object(log_projects, "backfill", side_effect=RuntimeError("down")), \
                patch.object(log_projects, "backfill_workspaces") as backfill_workspaces:
            log_projects.run_backfill_once()
        backfill_workspaces.assert_not_called()
        self.assertEqual(stored, {})


class TestSpan(_Db):

    def test_the_inference_span_names_the_workspace(self):
        from shared.callbacks.token_usage_callback import _set_config_identity
        from shared.utils.agent_config_identity import identify, record_loaded
        session = self.Session()
        session.add_all([Project(id=1, name="Shop"),
                         AgentConfig(id=1, name="seller", type="llm", project_id=1)])
        session.commit()
        identity = identify(session, session.get(AgentConfig, 1))
        session.close()
        self.assertEqual(identity["workspace_id"], 1)

        record_loaded("seller", identity)
        span = MagicMock()
        _set_config_identity(span, "seller")
        span.set_attribute.assert_any_call("mate.workspace.id", 1)


if __name__ == "__main__":
    unittest.main()
