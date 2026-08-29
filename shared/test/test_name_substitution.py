#!/usr/bin/env python3
"""
Tests for agent-name substitution during template import and sync (#74).

Four copies of a `sub_names` helper rewrote names with a loop of `str.replace`.
That has two defects: it rewrites text a previous replacement produced, and it
has no notion of a whole word. With agents `support` and `support_billing`,
mapping `support` first also rewrote the middle of `support_billing`. Whether
it happened depended on dict ordering, so it was intermittent.

All four now share `make_name_substituter`, which does one pass with word
boundaries, longest name first.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.dashboard.dashboard_server import make_name_substituter


class TestMakeNameSubstituter(unittest.TestCase):

    def test_nested_name_is_not_corrupted(self):
        # The original bug, stated directly.
        sub = make_name_substituter({"support": "support_acme",
                                     "support_billing": "support_billing_acme"})
        self.assertEqual(sub("support and support_billing"),
                         "support_acme and support_billing_acme")

    def test_longest_match_wins_regardless_of_map_order(self):
        # Sequential replace was order-dependent; this must not be.
        forward = make_name_substituter({"a_b": "X", "a_b_c": "Y"})
        reverse = make_name_substituter({"a_b_c": "Y", "a_b": "X"})
        self.assertEqual(forward("a_b_c"), "Y")
        self.assertEqual(reverse("a_b_c"), "Y")

    def test_a_replacement_is_not_itself_rewritten(self):
        # Looping replace would map alpha -> beta, then beta -> gamma.
        sub = make_name_substituter({"alpha": "beta", "beta": "gamma"})
        self.assertEqual(sub("alpha"), "beta")

    def test_word_boundaries_are_respected(self):
        sub = make_name_substituter({"bot": "robot"})
        self.assertEqual(sub("bot"), "robot")
        self.assertEqual(sub("sandbot"), "sandbot")
        self.assertEqual(sub("bots"), "bots")

    def test_names_with_regex_metacharacters_are_literal(self):
        sub = make_name_substituter({"a.b": "X"})
        self.assertEqual(sub("a.b"), "X")
        self.assertEqual(sub("axb"), "axb")

    def test_recurses_through_lists_and_dicts(self):
        sub = make_name_substituter({"old": "new"})
        self.assertEqual(sub(["old", {"k": "old"}]), ["new", {"k": "new"}])

    def test_non_string_values_pass_through(self):
        sub = make_name_substituter({"old": "new"})
        self.assertEqual(sub({"n": 5, "b": True, "z": None}), {"n": 5, "b": True, "z": None})

    def test_empty_map_is_an_identity(self):
        # An empty alternation would compile to a pattern matching everywhere.
        sub = make_name_substituter({})
        self.assertEqual(sub("support and support_billing"), "support and support_billing")


class TestTemplateImportNestedNames(unittest.TestCase):
    """The bug reaching a real caller: importing a template whose names nest."""

    @patch("shared.utils.database_client.get_database_client")
    def test_import_does_not_corrupt_nested_agent_names(self, mock_get_db):
        from fastapi import FastAPI
        from shared.utils.dashboard.dashboard_server import DashboardServer

        mock_get_db.return_value = MagicMock()
        project_root = Path(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        server = DashboardServer(app=FastAPI(), project_root=project_root)
        server._create_project = MagicMock(return_value={"id": 42})
        server._copy_template_agent = MagicMock()

        created = []
        server._create_agent_config = MagicMock(
            side_effect=lambda cfg, changed_by=None: created.append(cfg) or True)

        template = {
            "template_meta": {"id": "t", "agent_prefix": "tpl_", "root_agent": "tpl_support"},
            "agents": [
                {"name": "tpl_support", "type": "llm", "parent_agents": [],
                 "instruction": "Delegate billing to tpl_support_billing."},
                {"name": "tpl_support_billing", "type": "llm",
                 "parent_agents": ["tpl_support"], "instruction": "Handle billing."},
            ],
        }
        result = server._import_template(template_dict=template, project_name="Acme")
        self.assertNotIn("error", result)

        by_old = {c["name"]: c for c in created}
        self.assertEqual(len(created), 2)

        root = next(c for c in created if c["parent_agents"] == [])
        child = next(c for c in created if c["parent_agents"] != [])

        # The child's own name must not have the root's replacement spliced into it.
        self.assertTrue(child["name"].endswith("_billing"), child["name"])
        # The root's instruction must point at the child's real new name.
        self.assertIn(child["name"], root["instruction"])
        # And the child must be parented to the root's real new name.
        self.assertEqual(child["parent_agents"], [root["name"]])


if __name__ == "__main__":
    unittest.main()
