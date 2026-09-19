#!/usr/bin/env python3
"""
Unit tests for PDF and text attachment preprocessing and model capability validation.
"""

import unittest
import sys
import os
import base64
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.widget_routes import (
    extract_text_from_pdf_base64,
    extract_text_from_text_base64,
    model_supports_vision
)


class TestModelSupportsVision(unittest.TestCase):
    """
    The contract: refuse only on positive knowledge that a model is text-only.

    These patch LiteLLM rather than asserting its dataset — the map is refreshed
    from upstream, so pinning a test to what it currently says about a given
    model would make the suite fail on someone else's release.
    """

    def _info(self, value):
        return patch("litellm.get_model_info", return_value={"supports_vision": value})

    def test_a_vision_model_is_allowed(self):
        with self._info(True):
            self.assertTrue(model_supports_vision("some-vision-model"))

    def test_a_known_text_only_model_is_refused(self):
        with self._info(False):
            self.assertFalse(model_supports_vision("some-text-model"))

    def test_no_opinion_is_not_a_refusal(self):
        # LiteLLM knows the model but does not populate the field. That is not
        # evidence of anything, so the provider gets to answer.
        with self._info(None):
            self.assertTrue(model_supports_vision("some-model"))

    def test_a_model_litellm_does_not_know_is_allowed(self):
        # A custom or self-hosted endpoint cannot be judged from its name; the
        # substring allowlist this replaced refused every one of them.
        with patch("litellm.get_model_info", side_effect=Exception("not found")):
            self.assertTrue(model_supports_vision("my-self-hosted-thing"))

    def test_unset_model_defaults_to_true(self):
        self.assertTrue(model_supports_vision(""))
        self.assertTrue(model_supports_vision(None))

    def test_a_current_vision_model_is_allowed_for_real(self):
        # One unpatched check, on ids stable enough to rely on. gpt-4-turbo and
        # claude-sonnet-4-5 are the regression: the old heuristic refused both.
        for name in ("gpt-4o", "gpt-4-turbo", "claude-sonnet-4-5"):
            self.assertTrue(model_supports_vision(name), name)


class TestTextExtraction(unittest.TestCase):
    """Test extracting text from base64 files."""

    def test_extract_text_from_text_base64(self):
        original_text = "Hello, this is a plain text file attachment!"
        base64_data = base64.b64encode(original_text.encode("utf-8")).decode("utf-8")
        result = extract_text_from_text_base64(base64_data)
        self.assertEqual(result, original_text)

    def test_extract_text_from_text_base64_invalid(self):
        result = extract_text_from_text_base64("invalid_base64_%%%")
        self.assertTrue(result.startswith("[Error decoding text file:"))

    @patch("PyPDF2.PdfReader")
    def test_extract_text_from_pdf_base64(self, mock_pdf_reader_cls):
        # Mock PyPDF2 PdfReader page extraction
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "This is text from page 1"
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        mock_pdf_reader_cls.return_value = mock_reader

        # Create dummy pdf base64 (just a dummy string is fine as we are mocking PdfReader)
        dummy_base64 = base64.b64encode(b"%PDF-1.4 ...").decode("utf-8")
        result = extract_text_from_pdf_base64(dummy_base64)
        
        self.assertEqual(result, "This is text from page 1")
        mock_pdf_reader_cls.assert_called_once()

    @patch("PyPDF2.PdfReader")
    def test_extract_text_from_pdf_base64_empty_pages(self, mock_pdf_reader_cls):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        mock_pdf_reader_cls.return_value = mock_reader

        dummy_base64 = base64.b64encode(b"%PDF-1.4 ...").decode("utf-8")
        result = extract_text_from_pdf_base64(dummy_base64)
        
        self.assertEqual(result, "")

    def test_extract_text_from_pdf_base64_invalid(self):
        # Invalid pdf base64 that causes PyPDF2 to raise an error
        result = extract_text_from_pdf_base64("invalid_pdf_base64_data")
        self.assertTrue(result.startswith("[Error extracting text from PDF:"))


if __name__ == "__main__":
    unittest.main()
