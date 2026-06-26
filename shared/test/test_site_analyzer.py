#!/usr/bin/env python3
"""Unit tests for the wizard site analyzer (mocked LLM)."""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.utils.wizard.site_analyzer as sa


def _resp(content):
    m = MagicMock()
    m.choices = [MagicMock()]
    m.choices[0].message.content = content
    return m


PAGES = [{"url": "https://x.com", "title": "Salon", "text": "We offer haircuts and coloring."}]


class TestSiteAnalyzer(unittest.TestCase):

    def test_extract_json_plain(self):
        self.assertEqual(sa._extract_json('{"a": 1}'), {"a": 1})

    def test_extract_json_fenced(self):
        self.assertEqual(sa._extract_json('```json\n{"a": 2}\n```'), {"a": 2})

    def test_extract_json_embedded(self):
        self.assertEqual(sa._extract_json('Here: {"a": 3} done'), {"a": 3})

    def test_empty_pages(self):
        self.assertEqual(sa.analyze_site([]), {})

    def test_analyze_parses_services(self):
        payload = '{"description": "A hair salon.", "services": ["Haircut", "Coloring", "  "]}'
        with patch("litellm.completion", return_value=_resp(payload)) as mock_c:
            out = sa.analyze_site(PAGES, site_url="https://x.com", model="test/model")
        self.assertEqual(out["description"], "A hair salon.")
        self.assertEqual(out["services"], ["Haircut", "Coloring"])  # blank stripped
        self.assertTrue(mock_c.called)

    def test_analyze_handles_llm_error(self):
        with patch("litellm.completion", side_effect=Exception("boom")):
            self.assertEqual(sa.analyze_site(PAGES, site_url="https://x.com"), {})

    def test_analyze_bad_json_returns_empty_services(self):
        with patch("litellm.completion", return_value=_resp("not json at all")):
            out = sa.analyze_site(PAGES)
        self.assertEqual(out.get("services"), [])


if __name__ == "__main__":
    unittest.main()
