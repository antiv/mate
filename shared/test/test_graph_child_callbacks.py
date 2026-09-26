#!/usr/bin/env python3
"""
An LLM agent inside a graph workflow runs under the same RBAC and guardrail
callbacks as any other agent.

AgentManager once carried a branch giving "sub-agents of graph agents" only
token-logging callbacks, to work around TaskGroup issues. The branch could never
run - it compared the agent's own type, passed in as parent_agent_type, with
'graph' - and was removed. This keeps a workaround like it from ever quietly
stripping a graph's children of their access checks and guardrails.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.models import AgentConfig


class TestGraphChildCallbacks(unittest.TestCase):

    def test_a_graph_child_gets_rbac_and_guardrails(self):
        from shared.callbacks.user_profile_callback import combined_user_profile_and_rbac_callback
        with patch("shared.utils.agent_manager.get_database_client", return_value=MagicMock()):
            from shared.utils.agent_manager import AgentManager
            manager = AgentManager()

        graph = AgentConfig(name="wf", type="graph", project_id=1, description="wf",
                            planner_config=json.dumps({"edges": [{"from": "START", "to": "child"}]}))
        child = AgentConfig(name="child", type="llm", project_id=1, description="child",
                            instruction="x", model_name="gemini-2.5-flash", parent_agents='["wf"]')
        manager.get_root_agent_by_name = lambda name, project_id=None: graph
        manager.get_subagents = lambda name: [child] if name == "wf" else []

        with patch.dict(os.environ, {"MATE_PLUGINS_ENABLED": "false"}), \
                patch("shared.utils.file_search_service.FileSearchService") as fs:
            fs.return_value.get_stores_for_agent.return_value = []
            manager.initialize_agent_hierarchy("wf")

        built = manager.initialized_agents["child"]
        self.assertIs(built.before_model_callback, combined_user_profile_and_rbac_callback)
        self.assertEqual(built.after_model_callback.__name__, "_combined_after_with_guardrails")


if __name__ == "__main__":
    unittest.main()
