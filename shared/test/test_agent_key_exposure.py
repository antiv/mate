#!/usr/bin/env python3
"""
Unit tests keeping an agent's endpoint key on the server.

1.2.0 masked the key in the export, but the agent list the dashboard pages embed
and GET /dashboard/api/agents return, the version history, the rollback response
and the read_agent tool all carried it in clear. The list and history are
readable by any signed-in user, and read_agent hands its result to the model.
The stored key itself must survive: a save that posts the mask back keeps it,
and a rollback restores the real one.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.models import AgentConfig, AgentConfigVersion, Base
from shared.utils.utils import STORED_SECRET_SENTINEL, mask_config_secrets

PROJECT_ROOT = Path(__file__).parent.parent.parent
KEY = "sk-live-secret"


class TestMaskConfigSecrets(unittest.TestCase):

    def test_a_literal_key_is_masked_and_the_input_left_alone(self):
        config = {"name": "a", "model_api_key": KEY}
        self.assertEqual(mask_config_secrets(config)["model_api_key"], STORED_SECRET_SENTINEL)
        self.assertEqual(config["model_api_key"], KEY)

    def test_an_env_reference_and_no_key_pass_through(self):
        self.assertEqual(mask_config_secrets({"model_api_key": "${K}"})["model_api_key"], "${K}")
        self.assertIsNone(mask_config_secrets({"model_api_key": None})["model_api_key"])
        self.assertEqual(mask_config_secrets({"name": "a"}), {"name": "a"})


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db_client = MagicMock()
        self.db_client.is_connected.return_value = True
        self.db_client.get_session.side_effect = lambda: self.Session()
        self.addCleanup(self.engine.dispose)

        session = self.Session()
        agent = AgentConfig(name="ext_bot", type="llm", model_name="openai/x", instruction="v1",
                            model_base_url="http://127.0.0.1:9/v1", model_api_key=KEY, project_id=1)
        session.add(agent)
        session.flush()
        snapshot = {"name": "ext_bot", "type": "llm", "model_name": "openai/x",
                    "instruction": "v1", "model_api_key": KEY, "project_id": 1}
        version = AgentConfigVersion(agent_config_id=agent.id, version_number=1,
                                     config_snapshot=json.dumps(snapshot))
        session.add(version)
        session.commit()
        self.agent_id, self.version_id = agent.id, version.id
        session.close()

    def _stored_key(self):
        session = self.Session()
        try:
            return session.query(AgentConfig).get(self.agent_id).model_api_key
        finally:
            session.close()


class TestDashboard(_Db):

    def setUp(self):
        super().setUp()
        from shared.utils.dashboard.dashboard_server import DashboardServer
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=self.db_client):
            self.server = DashboardServer(app, project_root=PROJECT_ROOT)
        self.server.db_client = self.db_client
        self.server.AgentConfig, self.server.AgentConfigVersion = AgentConfig, AgentConfigVersion
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "someone"
        self.client = TestClient(app)

    def test_the_agent_list_does_not_carry_the_key(self):
        body = self.client.get("/dashboard/api/agents").text
        self.assertNotIn(KEY, body)
        configs = json.loads(body)["configs"]
        self.assertEqual(configs[0]["model_api_key"], STORED_SECRET_SENTINEL)

    def test_the_version_history_does_not_carry_the_key(self):
        body = self.client.get(f"/dashboard/api/agents/{self.agent_id}/versions").text
        self.assertNotIn(KEY, body)
        self.assertEqual(json.loads(body)["versions"][0]["config_snapshot"]["model_api_key"],
                         STORED_SECRET_SENTINEL)

    def test_a_rollback_restores_the_real_key_without_returning_it(self):
        session = self.Session()
        session.query(AgentConfig).get(self.agent_id).model_api_key = "sk-other"
        session.commit()
        session.close()

        result = self.server._rollback_agent_config(self.agent_id, self.version_id)
        self.assertEqual(result["model_api_key"], STORED_SECRET_SENTINEL)
        self.assertEqual(self._stored_key(), KEY)


class TestReadAgentTool(_Db):

    def test_the_model_never_sees_the_key(self):
        from shared.utils.tools import create_agent_tool
        context = SimpleNamespace(agent_name="ext_bot")
        with patch.object(create_agent_tool, "get_database_client", return_value=self.db_client), \
                patch.object(create_agent_tool, "_is_admin_user", return_value=True):
            result = create_agent_tool.read_agent(tool_context=context)
        self.assertEqual(result["status"], "success")
        self.assertNotIn(KEY, json.dumps(result, default=str))


if __name__ == "__main__":
    unittest.main()
