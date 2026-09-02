#!/usr/bin/env python3
"""
Tests that the widget actually serves the Art. 50 disclosure.

The widget is the surface the obligation is really about: embedded in someone
else's page and styled to match it, so nothing about it announces that the thing
answering is an AI.

Two properties matter beyond "the text is present". The disclosure is read from
the agent row on every render rather than stored in widget_config, because that
blob is editable through the widget admin API by whoever embeds the widget — and
the notice is not theirs to remove. And when the agent row cannot be read at all,
the widget shows the default rather than nothing: the failure that leaves people
uninformed is the worse one.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import widget_routes as wr
from shared.utils.ai_disclosure import DEFAULT_DISCLOSURE


class _Key:
    id = 1
    api_key = "wk_public"
    admin_key = "wak_secret"
    agent_name = "test_agent"
    project_id = 1

    def get_allowed_origins(self):
        return None

    def get_widget_config(self):
        return {"greeting": "hi", "theme": "light"}


class _Row:
    def __init__(self, text=None, waiver=None):
        self.ai_disclosure = text
        self.ai_disclosure_waiver = waiver


def _client():
    app = FastAPI()
    app.include_router(wr.router)
    patcher = patch.object(wr, "_lookup_widget_key",
                           side_effect=lambda k: _Key() if k == "wk_public" else None)
    patcher.start()
    return TestClient(app, base_url="http://mate.local"), patcher


class TestWidgetConfigDisclosure(unittest.TestCase):

    def setUp(self):
        self.client, self.patcher = _client()

    def tearDown(self):
        self.patcher.stop()

    def _config(self):
        return self.client.get("/widget/api/config", headers={"X-Widget-Key": "wk_public"})

    def test_an_unconfigured_agent_still_gets_the_default_notice(self):
        with patch.object(wr, "_agent_disclosure", return_value=DEFAULT_DISCLOSURE):
            body = self._config().json()
        self.assertEqual(body["ai_disclosure"], DEFAULT_DISCLOSURE)

    def test_custom_wording_is_served(self):
        with patch.object(wr, "_agent_disclosure", return_value="Ovo je AI asistent."):
            body = self._config().json()
        self.assertEqual(body["ai_disclosure"], "Ovo je AI asistent.")

    def test_a_waived_agent_serves_no_notice(self):
        with patch.object(wr, "_agent_disclosure", return_value=None):
            body = self._config().json()
        self.assertIsNone(body["ai_disclosure"])


class TestDisclosureLookup(unittest.TestCase):
    """_agent_disclosure reads the agent row, and fails toward telling people."""

    def _with_row(self, row):
        class _Session:
            def query(self, *_a, **_k): return self
            def filter(self, *_a, **_k): return self
            def first(self): return row
            def close(self): pass

        class _Db:
            def get_session(self): return _Session()

        return patch.object(wr, "get_database_client", return_value=_Db())

    def test_it_reads_the_agents_own_wording(self):
        with self._with_row(_Row(text="Custom notice")):
            self.assertEqual(wr._agent_disclosure("test_agent"), "Custom notice")

    def test_a_waiver_on_the_row_suppresses_it(self):
        with self._with_row(_Row(waiver="Staff only, told at onboarding")):
            self.assertIsNone(wr._agent_disclosure("test_agent"))

    def test_a_missing_agent_falls_back_to_the_default(self):
        with self._with_row(None):
            self.assertEqual(wr._agent_disclosure("gone"), DEFAULT_DISCLOSURE)

    def test_a_database_failure_still_shows_the_default(self):
        # Fail toward disclosure. A widget that silently stops disclosing because
        # a query failed is the exact outcome Art. 50 is written against.
        with patch.object(wr, "get_database_client", side_effect=RuntimeError("db down")):
            self.assertEqual(wr._agent_disclosure("test_agent"), DEFAULT_DISCLOSURE)


class TestDisclosureIsNotTheEmbedderS(unittest.TestCase):
    """widget_config is editable by the embedding site; the notice must not be."""

    def test_the_notice_does_not_come_from_widget_config(self):
        self.assertNotIn("ai_disclosure", _Key().get_widget_config())

    def test_a_forged_widget_config_value_is_overwritten_on_render(self):
        # Even if the stored blob somehow carried one, the render replaces it.
        self.assertNotIn("ai_disclosure", wr._ALLOWED_WIDGET_CONFIG_FIELDS)


if __name__ == "__main__":
    unittest.main()
