#!/usr/bin/env python3
"""
Unit test: an agent change made through the widget admin API is versioned and
audited like a dashboard edit. It changed the instruction or model with neither,
so there was nothing to roll back to and no record of who changed it.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.models import AgentConfig, AgentConfigVersion, Base


class TestWidgetAgentUpdate(unittest.TestCase):

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.addCleanup(engine.dispose)
        session = self.Session()
        session.add(AgentConfig(name="bot", type="llm", instruction="old", project_id=1))
        session.commit()
        session.close()

        db = MagicMock()
        db.get_session.side_effect = lambda: self.Session()
        from server import widget_routes
        app = FastAPI()
        app.include_router(widget_routes.admin_api_router)
        app.dependency_overrides[widget_routes.verify_widget_admin_key] = \
            lambda: SimpleNamespace(id=3, agent_name="bot", project_id=1)
        for p in (patch.object(widget_routes, "get_database_client", return_value=db),
                  patch("shared.utils.audit_service.log")):
            mocked = p.start()
            self.addCleanup(p.stop)
        self.audit = mocked
        self.client = TestClient(app)

    def test_a_change_is_versioned_and_audited(self):
        resp = self.client.put("/widget/api/agent", json={"instruction": "new"})
        self.assertEqual(resp.status_code, 200)
        session = self.Session()
        versions = session.query(AgentConfigVersion).all()
        session.close()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].get_snapshot()["instruction"], "new")
        self.assertEqual(versions[0].changed_by, "widget_admin:3")
        self.assertEqual(self.audit.call_args.args[0], "widget_admin:3")
        self.assertEqual(self.audit.call_args.kwargs["details"], {"fields": ["instruction"]})

    def test_a_request_that_changes_nothing_records_nothing(self):
        self.client.put("/widget/api/agent", json={"unrelated": 1})
        session = self.Session()
        self.assertEqual(session.query(AgentConfigVersion).count(), 0)
        session.close()
        self.audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
