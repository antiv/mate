#!/usr/bin/env python3
"""
Tests for refusing code_executor on widget-exposed agents (#72).

The executor is explicitly not a sandbox, so granting it to an agent that
anonymous site visitors can prompt through a widget key hands them shell access
to the host. The guard has to fail closed: when it cannot determine exposure it
refuses the tool, because the failure mode of guessing wrong in the other
direction is remote code execution.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import Base, Project, WidgetApiKey
from shared.utils.tools.tool_factory import ToolFactory

CODE_EXECUTOR_TOOLS = {"execute_python_code", "execute_shell_command"}


class TestCodeExecutorWidgetGuard(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.db_client = MagicMock()
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.patcher = patch("shared.utils.database_client.get_database_client",
                             return_value=self.db_client)
        self.patcher.start()

        session = self.Session()
        session.add(Project(id=1, name="P"))
        session.commit()
        session.close()

        self.factory = ToolFactory()
        self.config = {"name": "agent_with_executor",
                       "tool_config": json.dumps({"code_executor": True})}

    def tearDown(self):
        self.patcher.stop()
        self.engine.dispose()

    def _add_widget_key(self, agent_name, is_active=True):
        session = self.Session()
        session.add(WidgetApiKey(api_key=f"key_{agent_name}_{is_active}", project_id=1,
                                 agent_name=agent_name, is_active=is_active))
        session.commit()
        session.close()

    def _executor_tools(self, config=None):
        tools = self.factory.create_tools(config or self.config)
        return CODE_EXECUTOR_TOOLS.intersection(
            {getattr(t, "__name__", str(t)) for t in tools})

    def test_agent_without_widget_key_gets_the_tools(self):
        self.assertEqual(self._executor_tools(), CODE_EXECUTOR_TOOLS)

    def test_agent_with_widget_key_is_refused(self):
        self._add_widget_key("agent_with_executor")
        self.assertEqual(self._executor_tools(), set())

    def test_inactive_widget_key_still_refuses(self):
        # Reactivating a key is a dashboard toggle that never touches the agent,
        # so an inactive key is exposure waiting to happen, not absence of it.
        self._add_widget_key("agent_with_executor", is_active=False)
        self.assertEqual(self._executor_tools(), set())

    def test_key_for_a_different_agent_does_not_refuse(self):
        self._add_widget_key("some_other_agent")
        self.assertEqual(self._executor_tools(), CODE_EXECUTOR_TOOLS)

    def test_override_env_var_restores_the_tools(self):
        self._add_widget_key("agent_with_executor")
        with patch.dict(os.environ, {"MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET": "true"}):
            self.assertEqual(self._executor_tools(), CODE_EXECUTOR_TOOLS)

    def test_override_is_off_for_other_values(self):
        self._add_widget_key("agent_with_executor")
        for value in ("", "false", "no", "0"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET": value}):
                    self.assertEqual(self._executor_tools(), set())

    def test_database_failure_refuses_rather_than_grants(self):
        self.db_client.get_session.side_effect = RuntimeError("db down")
        self.assertEqual(self._executor_tools(), set())

    def test_missing_database_client_refuses(self):
        with patch("shared.utils.database_client.get_database_client", return_value=None):
            self.assertEqual(self._executor_tools(), set())

    def test_other_tools_are_unaffected_by_the_refusal(self):
        self._add_widget_key("agent_with_executor")
        tools = self.factory.create_tools(self.config)
        names = {getattr(t, "__name__", str(t)) for t in tools}
        # user_profile tools are added to every agent; the refusal must not
        # take the rest of the agent's toolset down with it.
        self.assertIn("get_user_profile", names)


if __name__ == "__main__":
    unittest.main()
