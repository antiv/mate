#!/usr/bin/env python3
"""
Unit tests for the widget message context prefix: the visitor's language must always
reach the agent, while page URL/title stay behind the context_injection opt-in.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.widget_routes import _build_context_prefix

PAGE = {"url": "https://example.com/game", "title": "Mystery", "lang": "en"}


class TestWidgetContextPrefix(unittest.TestCase):

    def test_language_injected_without_context_injection(self):
        # Multilingual demo pages rely on this: no opt-in, but the agent must
        # still answer in the visitor's language.
        prefix = _build_context_prefix({}, PAGE, "en")
        self.assertIn("[User language: English (en)]", prefix)
        self.assertNotIn("Page context", prefix)

    def test_serbian_language_name_resolved(self):
        self.assertIn("[User language: Serbian (sr)]", _build_context_prefix({}, None, "sr"))

    def test_unknown_language_code_passed_through(self):
        self.assertIn("[User language: xx (xx)]", _build_context_prefix({}, None, "xx"))

    def test_page_context_requires_opt_in(self):
        prefix = _build_context_prefix({"context_injection": True}, PAGE, "en")
        self.assertIn("Page context", prefix)
        self.assertIn("[User language: English (en)]", prefix)

    def test_empty_when_no_language_and_no_context(self):
        self.assertEqual(_build_context_prefix({}, None, ""), "")
        self.assertEqual(_build_context_prefix({"context_injection": True}, None, ""), "")

    def test_prefix_ends_with_blank_line(self):
        self.assertTrue(_build_context_prefix({}, None, "de").endswith("\n\n"))

    def test_non_dict_page_context_ignored(self):
        prefix = _build_context_prefix({"context_injection": True}, "not-a-dict", "en")
        self.assertNotIn("Page context", prefix)
        self.assertIn("[User language: English (en)]", prefix)


if __name__ == '__main__':
    unittest.main()
