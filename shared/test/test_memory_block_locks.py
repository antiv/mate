#!/usr/bin/env python3
"""
Read-only and Character Limit on memory blocks are enforced (#153).

Both were stored in the block's metadata and never checked, so an agent, the
dashboard, the widget admin API or a trigger could overwrite or delete a
read-only block and write past the limit. The service now refuses both, so
every caller is covered; a write that clears ``read_only`` is how a block is
unlocked.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.test.test_memory_block_versions import PROJECT_ROOT, FakeDbClient, _no_embeddings
from shared.utils.memory_blocks_service import MemoryBlocksService
from shared.utils.models import AgentConfig


class LockTestCase(unittest.TestCase):

    def setUp(self):
        _no_embeddings(self)
        self.db = FakeDbClient()
        self.addCleanup(self.db.engine.dispose)
        self.svc = MemoryBlocksService(self.db)
        self.block_id = self.svc.create_block(
            1, "policy", "Refunds within 30 days", metadata={"read_only": True, "owner": "legal"},
        )["block_id"]

    def value(self, label="policy"):
        return self.svc.get_block(1, label)["value"]


class TestReadOnly(LockTestCase):

    def test_a_value_change_is_refused(self):
        result = self.svc.modify_block(1, "policy", value="Refunds never")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "read_only")
        self.assertIn("read-only", result["error_message"])
        self.assertEqual(self.value(), "Refunds within 30 days")

    def test_keeping_the_flag_while_changing_metadata_is_refused(self):
        result = self.svc.modify_block(1, "policy", value="x", metadata={"read_only": True})
        self.assertEqual(result["error_code"], "read_only")

    def test_delete_is_refused(self):
        result = self.svc.delete_block(1, "policy")
        self.assertEqual(result["error_code"], "read_only")
        self.assertEqual(self.svc.get_block(1, "policy")["status"], "success")

    def test_restore_is_refused(self):
        version_id = self.svc.list_versions(1, self.block_id)["versions"][0]["id"]
        result = self.svc.restore_version(1, version_id)
        self.assertEqual(result["error_code"], "read_only")

    def test_clearing_the_flag_unlocks_the_block_and_keeps_other_metadata(self):
        result = self.svc.modify_block(1, "policy", value="Refunds within 14 days",
                                       metadata_updates={"read_only": None})
        self.assertEqual(result["status"], "success", result)
        block = self.svc.get_block(1, "policy")
        self.assertEqual(block["value"], "Refunds within 14 days")
        self.assertEqual(block["metadata"], {"owner": "legal"})
        self.assertEqual(self.svc.delete_block(1, "policy")["status"], "success")

    def test_a_block_can_be_locked_in_the_same_write(self):
        self.svc.create_block(1, "open", "a")
        result = self.svc.modify_block(1, "open", value="b", metadata_updates={"read_only": True})
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.svc.modify_block(1, "open", value="c")["error_code"], "read_only")


class TestCharacterLimit(LockTestCase):

    def setUp(self):
        super().setUp()
        self.svc.create_block(1, "bio", "short", metadata={"limit": 10})

    def test_create_over_the_limit_is_refused(self):
        result = self.svc.create_block(1, "long", "x" * 11, metadata={"limit": 10})
        self.assertEqual(result["error_code"], "too_long")
        self.assertEqual(result["limit"], 10)
        self.assertEqual(self.svc.get_block(1, "long")["status"], "error")

    def test_modify_over_the_limit_is_refused_with_a_message_an_agent_can_act_on(self):
        result = self.svc.modify_block(1, "bio", value="x" * 11)
        self.assertEqual(result["error_code"], "too_long")
        self.assertIn("11 characters", result["error_message"])
        self.assertIn("at most 10", result["error_message"])
        self.assertEqual(self.value("bio"), "short")

    def test_a_value_at_the_limit_is_accepted(self):
        self.assertEqual(self.svc.modify_block(1, "bio", value="x" * 10)["status"], "success")

    def test_lowering_the_limit_below_the_current_value_is_refused(self):
        result = self.svc.modify_block(1, "bio", metadata_updates={"limit": 3})
        self.assertEqual(result["error_code"], "too_long")

    def test_a_description_change_on_an_older_oversized_block_is_allowed(self):
        # Saved before limits were enforced; only a value or limit change is checked.
        self.svc.create_block(1, "legacy", "x" * 20)
        self.svc.modify_block(1, "legacy", metadata={"limit": 50})
        session = self.db.get_session()
        from shared.utils.models import MemoryBlock
        session.query(MemoryBlock).filter_by(label="legacy").one().block_metadata = '{"limit": 5}'
        session.commit()
        session.close()
        result = self.svc.modify_block(1, "legacy", description="kept as is")
        self.assertEqual(result["status"], "success", result)

    def test_a_limit_stored_as_text_is_honoured_and_garbage_is_ignored(self):
        self.svc.create_block(1, "text_limit", "abc", metadata={"limit": "3"})
        self.assertEqual(self.svc.modify_block(1, "text_limit", value="abcd")["error_code"], "too_long")
        self.svc.create_block(1, "bad_limit", "abc", metadata={"limit": "lots"})
        self.assertEqual(self.svc.modify_block(1, "bad_limit", value="abcd" * 10)["status"], "success")


class TestCallers(LockTestCase):

    def test_an_agent_tool_gets_the_refusal(self):
        from shared.utils.tools.memory_blocks_tools import create_memory_blocks_tools_from_config
        tools = {t.__name__: t for t in create_memory_blocks_tools_from_config({"project_id": 1, "name": "desk"})}
        context = SimpleNamespace(_invocation_context=SimpleNamespace(user_id="bob"))
        with patch("shared.utils.tools.memory_blocks_tools._is_admin_user", return_value=True), \
                patch("shared.utils.database_client.get_database_client", return_value=self.db):
            modify = tools["modify_shared_block"](block_id="policy", value="no", tool_context=context)
            delete = tools["delete_shared_block"](block_id="policy", tool_context=context)
        self.assertEqual(modify["error_code"], "read_only")
        self.assertEqual(delete["error_code"], "read_only")
        self.assertEqual(self.value(), "Refunds within 30 days")

    def test_a_trigger_writing_a_read_only_block_fails_the_run(self):
        from shared.utils.trigger_runner import TriggerRunner
        trigger = SimpleNamespace(id=7, name="digest", project_id=1)
        runner = TriggerRunner.__new__(TriggerRunner)
        with patch("shared.utils.database_client.get_database_client", return_value=self.db):
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                runner._output_memory_block(trigger, {"label": "policy"}, "overwritten")
            # A missing block is still created.
            runner._output_memory_block(trigger, {"label": "fresh"}, "new")
        self.assertEqual(self.value(), "Refunds within 30 days")
        self.assertEqual(self.value("fresh"), "new")


class TestDashboardEditForm(LockTestCase):

    def setUp(self):
        super().setUp()
        session = self.db.get_session()
        session.add(AgentConfig(name="desk", type="llm", project_id=1, tool_config='{"memory_blocks": true}'))
        session.commit()
        session.close()

        from shared.utils.dashboard.dashboard_server import DashboardServer
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=self.db):
            self.server = DashboardServer(app, project_root=PROJECT_ROOT)
        self.server.db_client = self.db
        self.server.AgentConfig = AgentConfig
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "admin"
        self.client = TestClient(app)

    def save(self, **form):
        # What the edit form posts: the checkboxes always, the limit only when set.
        data = {"label": "policy", "value": "Refunds within 14 days",
                "read_only": "true", "preserve_on_migration": "false", **form}
        return self.client.put(f"/dashboard/api/agents/desk/memory-blocks/{self.block_id}", data=data).json()

    def test_saving_with_read_only_still_checked_is_refused(self):
        result = self.save()
        self.assertFalse(result["success"])
        self.assertIn("read-only", result["error"])
        self.assertEqual(self.value(), "Refunds within 30 days")

    def test_unchecking_read_only_saves_the_change(self):
        result = self.save(read_only="false")
        self.assertTrue(result["success"], result)
        block = self.svc.get_block(1, "policy")
        self.assertEqual(block["value"], "Refunds within 14 days")
        self.assertEqual(block["metadata"], {"owner": "legal"})

    def test_the_form_now_saves_the_limit(self):
        self.save(read_only="false", character_limit="40")
        self.assertEqual(self.svc.get_block(1, "policy")["metadata"]["limit"], 40)
        result = self.save(read_only="false", character_limit="5")
        self.assertFalse(result["success"])
        self.assertIn("at most 5", result["error"])


if __name__ == "__main__":
    unittest.main()
