"""
features_in_use: whether the wizard and the shop's stored orders are in use, which the
Control Room sidebar reads to show their pages (documents/design/dashboard-2.1.md).
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.utils import features_in_use
from shared.utils.models import (
    AgentConfig, Base, Project, ShopOrder, WizardLead, WizardPartner, WizardSession,
)


class TestFeaturesInUse(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = MagicMock()
        db.get_session.side_effect = lambda: self.Session()
        self.patcher = patch("shared.utils.database_client.get_database_client", return_value=db)
        self.patcher.start()
        self._add(Project(id=1, name="p"))
        features_in_use.clear_cache()

    def tearDown(self):
        features_in_use.clear_cache()
        self.patcher.stop()
        self.engine.dispose()

    def _add(self, *rows):
        session = self.Session()
        session.add_all(rows)
        session.commit()
        session.close()

    def _agent(self, name, tool_config):
        return AgentConfig(name=name, type="llm", project_id=1, tool_config=json.dumps(tool_config))

    def test_an_empty_installation_uses_neither(self):
        self.assertEqual(features_in_use.in_use(), {"wizard": False, "shop": False})

    def test_the_wizard_is_in_use_with_a_partner_a_lead_or_a_session(self):
        for row in (WizardPartner(partner_key="site"), WizardLead(email="a@example.com"),
                    WizardSession(session_token="t")):
            with self.subTest(row=type(row).__name__):
                session = self.Session()
                for model in (WizardPartner, WizardLead, WizardSession):
                    session.query(model).delete()
                session.commit()
                session.close()
                self._add(row)
                features_in_use.clear_cache()
                self.assertTrue(features_in_use.in_use()["wizard"])

    def test_the_shop_is_in_use_with_a_stored_order(self):
        self._add(ShopOrder(order_id="ORD-1"))
        self.assertEqual(features_in_use.in_use(), {"wizard": False, "shop": True})

    def test_the_shop_is_in_use_when_an_agent_s_shop_sets_a_partner_key(self):
        self._add(self._agent("seller", {"shop": {"catalog": [], "partner_key": "salon"}}))
        self.assertTrue(features_in_use.in_use()["shop"])

    def test_a_shop_without_a_partner_key_stores_nothing(self):
        # Wizard trial agents have a shop, but no partner_key: their orders aren't stored
        self._add(self._agent("trial", {"shop": {"catalog": [], "partner_key": " "}}),
                  self._agent("other", {"mcp": {"partner_key": "not-a-shop"}}))
        self.assertFalse(features_in_use.in_use()["shop"])

    def test_the_answer_is_cached(self):
        self.assertFalse(features_in_use.in_use()["shop"])
        self._add(ShopOrder(order_id="ORD-1"))
        self.assertFalse(features_in_use.in_use()["shop"])
        features_in_use.clear_cache()
        self.assertTrue(features_in_use.in_use()["shop"])

    def test_a_database_error_shows_neither(self):
        with patch.object(features_in_use, "_query", side_effect=RuntimeError("down")):
            self.assertEqual(features_in_use.in_use(), {"wizard": False, "shop": False})


if __name__ == "__main__":
    unittest.main()
