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
    """Test model_supports_vision function."""

    def test_gemini_models_support_vision(self):
        self.assertTrue(model_supports_vision("gemini-2.0-flash"))
        self.assertTrue(model_supports_vision("google/gemini-2.5-pro"))
        self.assertTrue(model_supports_vision("gemini-1.5-pro-latest"))

    def test_openai_vision_models_support_vision(self):
        self.assertTrue(model_supports_vision("gpt-4o"))
        self.assertTrue(model_supports_vision("gpt-4o-mini"))
        self.assertTrue(model_supports_vision("gpt-4-vision-preview"))

    def test_claude_vision_models_support_vision(self):
        self.assertTrue(model_supports_vision("claude-3-opus-20240229"))
        self.assertTrue(model_supports_vision("claude-3-5-sonnet"))

    def test_vl_models_support_vision(self):
        self.assertTrue(model_supports_vision("qwen-vl-max"))
        self.assertTrue(model_supports_vision("internvl-chat-vl"))

    def test_text_only_models_do_not_support_vision(self):
        self.assertFalse(model_supports_vision("deepseek-chat"))
        self.assertFalse(model_supports_vision("deepseek-coder"))
        self.assertFalse(model_supports_vision("gpt-3.5-turbo"))
        self.assertFalse(model_supports_vision("llama-3-70b-instruct"))
        self.assertFalse(model_supports_vision("mixtral-8x7b-instruct"))

    def test_unknown_models_default_to_false(self):
        self.assertFalse(model_supports_vision("my-custom-model"))

    def test_unset_model_defaults_to_true(self):
        self.assertTrue(model_supports_vision(""))
        self.assertTrue(model_supports_vision(None))


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
