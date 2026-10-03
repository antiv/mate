#!/usr/bin/env python3
"""
Unit tests for validate_image_generation_setup with the google-genai SDK.
"""

import os
import sys
import unittest
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestValidateImageGenerationSetupGemini(unittest.TestCase):
    """The Gemini check must use google-genai's Client, not the old configure() API."""

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=True)
    def test_google_key_reports_gemini_model(self):
        from shared.utils.tools import image_tools
        with patch.object(image_tools.genai, "Client") as client_cls:
            ok, error, details = image_tools.validate_image_generation_setup()
        self.assertTrue(ok, error)
        self.assertEqual(error, "")
        client_cls.assert_called_once_with(api_key="test-key")
        self.assertEqual(details["api_key_source"], "GOOGLE_API_KEY")
        self.assertTrue(details["gemini_models_available"])


if __name__ == "__main__":
    unittest.main()
