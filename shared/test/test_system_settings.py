#!/usr/bin/env python3
"""
Server-wide settings from the dashboard, and the default image model page.

A stored value wins over the environment variable; clearing it falls back.
Only admins may read or change it, every change is audited, and a model name
LiteLLM cannot place is refused before it reaches an agent.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import system_settings as ss
from shared.utils.models import Base, SystemSetting

PROJECT_ROOT = Path(__file__).parent.parent.parent


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = MagicMock()
        self.db.get_session.side_effect = lambda: self.Session()
        p = patch("shared.utils.system_settings.get_database_client", return_value=self.db)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.engine.dispose)


class TestStore(_Db):

    def test_set_read_change_and_clear(self):
        self.assertIsNone(ss.get_setting(ss.IMAGE_MODEL))
        self.assertTrue(ss.set_setting(ss.IMAGE_MODEL, "gpt-image-1", "admin"))
        self.assertEqual(ss.get_setting(ss.IMAGE_MODEL), "gpt-image-1")
        self.assertTrue(ss.set_setting(ss.IMAGE_MODEL, "recraft/recraftv3", "ana"))
        session = self.Session()
        row = session.get(SystemSetting, ss.IMAGE_MODEL)
        self.assertEqual((row.value, row.updated_by), ("recraft/recraftv3", "ana"))
        session.close()
        self.assertTrue(ss.set_setting(ss.IMAGE_MODEL, "", "ana"))
        self.assertIsNone(ss.get_setting(ss.IMAGE_MODEL))

    def test_no_database_reads_as_unset(self):
        self.db.get_session.side_effect = lambda: None
        self.assertIsNone(ss.get_setting(ss.IMAGE_MODEL))
        self.assertFalse(ss.set_setting(ss.IMAGE_MODEL, "x", "admin"))


class TestImageModelApi(_Db):

    def setUp(self):
        super().setUp()
        from shared.utils.dashboard.dashboard_server import DashboardServer
        app = FastAPI()
        with patch("shared.utils.database_client.get_database_client", return_value=self.db):
            self.server = DashboardServer(app, project_root=PROJECT_ROOT)
        app.dependency_overrides[self.server._get_auth_user_dependency] = lambda: "admin"
        self.is_admin = True
        self.server._get_is_admin = lambda request: self.is_admin
        self.audit = patch("shared.utils.audit_service.log").start()
        self.addCleanup(patch.stopall)
        self.client = TestClient(app)
        self.url = "/dashboard/api/settings/image-model"

    @patch.dict(os.environ, {"IMAGE_MODEL": "stability/sd3-large", "OPENAI_API_KEY": "sk"}, clear=True)
    def test_shows_where_the_default_comes_from(self):
        data = self.client.get(self.url).json()
        self.assertEqual((data["stored"], data["effective"], data["source"]),
                         ("", "stability/sd3-large", "IMAGE_MODEL"))
        data = self.client.put(self.url, json={"model": "gpt-image-1"}).json()
        self.assertEqual((data["stored"], data["effective"], data["source"]),
                         ("gpt-image-1", "gpt-image-1", "dashboard"))
        self.assertTrue(data["check"]["ok"])
        data = self.client.put(self.url, json={"model": ""}).json()
        self.assertEqual(data["source"], "IMAGE_MODEL")

    @patch.dict(os.environ, {}, clear=True)
    def test_a_missing_key_is_reported(self):
        data = self.client.put(self.url, json={"model": "dall-e-3"}).json()
        self.assertFalse(data["check"]["ok"])
        self.assertIn("OPENAI_API_KEY", data["check"]["missing_keys"])

    def test_bad_models_are_refused(self):
        for model in ("not a model", "x" * 300, 5, "nonsense-provider-xyz/thing"):
            resp = self.client.put(self.url, json={"model": model})
            self.assertEqual(resp.status_code, 400, model)
        self.assertIsNone(ss.get_setting(ss.IMAGE_MODEL))

    def test_changes_are_audited(self):
        self.client.put(self.url, json={"model": "gpt-image-1"})
        args, kwargs = self.audit.call_args
        self.assertEqual(args[:2], ("admin", "config.change"))
        self.assertEqual(kwargs["details"], {"before": None, "after": "gpt-image-1"})

    def test_admin_only(self):
        self.is_admin = False
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.put(self.url, json={"model": "gpt-image-1"}).status_code, 403)
        self.assertIsNone(ss.get_setting(ss.IMAGE_MODEL))


if __name__ == "__main__":
    unittest.main()
