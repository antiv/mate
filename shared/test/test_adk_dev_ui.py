#!/usr/bin/env python3
"""
Tests for when ADK's dev UI (/dev-ui) is served.

It is a debugging tool that shows admins every event, tool argument and session
state, and it runs on the dashboard's origin. Production leaves it off unless
ADK_DEV_UI=true asks for it; development keeps it on unless ADK_DEV_UI=false.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.utils import adk_dev_ui_enabled


class TestAdkDevUiEnabled(unittest.TestCase):

    def _enabled(self, **env):
        with patch.dict(os.environ, env):
            for name in ("ADK_DEV_UI", "MATE_ENV"):
                if name not in env:
                    os.environ.pop(name, None)
            return adk_dev_ui_enabled()

    def test_on_by_default_outside_production(self):
        self.assertTrue(self._enabled())
        self.assertTrue(self._enabled(MATE_ENV="development"))

    def test_off_by_default_in_production(self):
        self.assertFalse(self._enabled(MATE_ENV="production"))
        self.assertFalse(self._enabled(MATE_ENV=" Production "))

    def test_adk_dev_ui_overrides_either_way(self):
        self.assertTrue(self._enabled(MATE_ENV="production", ADK_DEV_UI="true"))
        self.assertFalse(self._enabled(MATE_ENV="development", ADK_DEV_UI="false"))
        for value in ("0", "no", "off", "FALSE"):
            with self.subTest(value=value):
                self.assertFalse(self._enabled(ADK_DEV_UI=value))

    def test_an_empty_value_means_the_default(self):
        self.assertFalse(self._enabled(MATE_ENV="production", ADK_DEV_UI=""))


if __name__ == "__main__":
    unittest.main()
