#!/usr/bin/env python3
"""
Unit tests for cloning an agent tree into another project (#19).

The operation is insert-only: its central promise is that the source hierarchy
is never modified, whatever happens. The other load-bearing parts are the name
rewrite — agent names are globally unique, so every clone is renamed, and
in-tree references in text fields must follow — and the tree walk, which has to
handle multi-parent sub-agents without duplicating or escaping the tree.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import (
    AgentConfig, AgentFileSearchStore, AuditLog, Base, FileSearchStore,
    MemoryBlock, Project,
)
from shared.utils.dashboard.dashboard_server import DashboardServer


class TestCloneAgentTree(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.mock_db_client = MagicMock()
        self.mock_db_client.get_session.side_effect = lambda: self.Session()
        self.mock_db_client.is_connected.return_value = True
        self.patcher = patch('shared.utils.database_client.get_database_client',
                             return_value=self.mock_db_client)
        self.patcher.start()
        self.audit_patcher = patch('shared.utils.audit_service.get_database_client',
                                   return_value=self.mock_db_client)
        self.audit_patcher.start()

        from fastapi import FastAPI
        project_root = Path(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        self.server = DashboardServer(FastAPI(), project_root)
        self.server.db_client = self.mock_db_client
        # Folder creation writes to the real agents/ directory — keep it off disk
        self.server._copy_template_agent = MagicMock(
            return_value={"success": True, "skipped": False})

        session = self.Session()
        session.add(Project(id=1, name="Source"))
        session.add(Project(id=2, name="Acme Support"))
        session.add(AgentConfig(name="support_root", type="llm", project_id=1,
                                instruction="Route billing to support_billing, tech to support_tech."))
        session.add(AgentConfig(name="support_billing", type="llm", project_id=1,
                                parent_agents='["support_root"]'))
        # Two in-tree parents — must be collected once, both parents mapped
        session.add(AgentConfig(name="support_tech", type="llm", project_id=1,
                                parent_agents='["support_root", "support_billing"]'))
        session.add(AgentConfig(name="other_root", type="llm", project_id=1))
        session.commit()
        session.close()

    def tearDown(self):
        self.patcher.stop()
        self.audit_patcher.stop()
        self.engine.dispose()

    def _clone(self, **kwargs):
        defaults = dict(root_name="support_root", target_project_id=2,
                        suffix="_acme", changed_by="tester")
        defaults.update(kwargs)
        return self.server._clone_agent_tree(**defaults)

    def _agents(self, project_id):
        session = self.Session()
        try:
            return {a.name: a for a in session.query(AgentConfig).filter(
                AgentConfig.project_id == project_id).all()}
        finally:
            session.close()

    def test_clones_the_whole_tree_with_rewritten_parents(self):
        result = self._clone()
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["new_root"], "support_root_acme")

        clones = self._agents(2)
        self.assertEqual(set(clones), {"support_root_acme", "support_billing_acme",
                                       "support_tech_acme"})
        self.assertEqual(clones["support_root_acme"].get_parent_agents(), [])
        self.assertEqual(clones["support_billing_acme"].get_parent_agents(),
                         ["support_root_acme"])
        self.assertEqual(clones["support_tech_acme"].get_parent_agents(),
                         ["support_root_acme", "support_billing_acme"])

    def test_out_of_tree_agents_are_not_cloned(self):
        self._clone()
        self.assertNotIn("other_root_acme", self._agents(2))

    def test_out_of_tree_parent_is_dropped(self):
        # The clone must form its own tree, never attach into the source hierarchy
        session = self.Session()
        session.add(AgentConfig(name="support_shared", type="llm", project_id=1,
                                parent_agents='["support_root", "other_root"]'))
        session.commit()
        session.close()
        self._clone()
        clones = self._agents(2)
        self.assertEqual(clones["support_shared_acme"].get_parent_agents(),
                         ["support_root_acme"])

    def test_source_rows_are_untouched(self):
        before = {n: (a.project_id, a.parent_agents, a.instruction)
                  for n, a in self._agents(1).items()}
        self._clone()
        after = {n: (a.project_id, a.parent_agents, a.instruction)
                 for n, a in self._agents(1).items()}
        self.assertEqual(before, after)

    def test_instruction_references_follow_the_rename(self):
        self._clone()
        instruction = self._agents(2)["support_root_acme"].instruction
        self.assertIn("support_billing_acme", instruction)
        self.assertIn("support_tech_acme", instruction)
        self.assertNotIn("support_billing,", instruction)

    def test_nested_names_survive_the_rewrite(self):
        # "support" is a prefix of "support_billing": sequential .replace would
        # corrupt the longer name mid-string. The word-boundary single pass must not.
        session = self.Session()
        session.add(AgentConfig(name="support", type="llm", project_id=1,
                                instruction="use support_billing then support"))
        session.add(AgentConfig(name="support_billing2", type="llm", project_id=1,
                                parent_agents='["support"]'))
        session.commit()
        session.close()
        result = self._clone(root_name="support")
        self.assertTrue(result.get("success"), result)
        instruction = self._agents(2)["support_acme"].instruction
        self.assertEqual(instruction, "use support_billing then support_acme")

    def test_name_collision_bumps_the_suffix(self):
        session = self.Session()
        session.add(AgentConfig(name="support_root_acme", type="llm", project_id=2))
        session.commit()
        session.close()
        result = self._clone()
        self.assertTrue(result.get("success"), result)
        self.assertEqual(result["new_root"], "support_root_acme_2")
        # The colliding agent is not touched
        self.assertIsNone(self._agents(2)["support_root_acme"].instruction)

    def test_folder_created_for_the_cloned_root_only(self):
        self._clone()
        self.server._copy_template_agent.assert_called_once_with("support_root_acme")

    def test_non_root_agent_is_rejected(self):
        result = self._clone(root_name="support_billing")
        self.assertEqual(result.get("status_code"), 400)
        self.assertEqual(self._agents(2), {})

    def test_unknown_target_project_is_rejected(self):
        result = self._clone(target_project_id=99)
        self.assertEqual(result.get("status_code"), 404)

    def test_empty_suffix_is_rejected(self):
        result = self._clone(suffix="   ")
        self.assertEqual(result.get("status_code"), 400)

    def test_memory_blocks_copied_and_existing_labels_kept(self):
        session = self.Session()
        session.add(MemoryBlock(project_id=1, label="faq", value="source faq"))
        session.add(MemoryBlock(project_id=1, label="policies", value="source policies"))
        session.add(MemoryBlock(project_id=2, label="faq", value="target faq"))
        session.commit()
        session.close()
        result = self._clone(include_memory_blocks=True)
        self.assertEqual(result["memory_blocks_copied"], 1)
        session = self.Session()
        target_blocks = {b.label: b.value for b in session.query(MemoryBlock).filter(
            MemoryBlock.project_id == 2).all()}
        session.close()
        # The pre-existing target block wins; only the missing label was copied
        self.assertEqual(target_blocks, {"faq": "target faq",
                                         "policies": "source policies"})

    def test_memory_blocks_not_copied_by_default(self):
        session = self.Session()
        session.add(MemoryBlock(project_id=1, label="faq", value="v"))
        session.commit()
        session.close()
        result = self._clone()
        self.assertEqual(result["memory_blocks_copied"], 0)

    def test_file_search_assignments_share_the_store(self):
        session = self.Session()
        session.add(FileSearchStore(id=5, project_id=1, store_name="stores/abc",
                                    display_name="docs"))
        session.add(AgentFileSearchStore(agent_name="support_billing", store_id=5,
                                         is_primary=True))
        session.commit()
        session.close()
        result = self._clone(include_file_search=True)
        self.assertEqual(result["file_search_assigned"], 1)
        session = self.Session()
        row = session.query(AgentFileSearchStore).filter(
            AgentFileSearchStore.agent_name == "support_billing_acme").one()
        session.close()
        # Same store — the remote Gemini store and its documents are shared
        self.assertEqual(row.store_id, 5)
        self.assertTrue(row.is_primary)

    def test_clone_is_audited(self):
        self._clone()
        session = self.Session()
        rows = session.query(AuditLog).filter(AuditLog.action == "agent.clone").all()
        session.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].resource_id, "support_root")


if __name__ == '__main__':
    unittest.main()
