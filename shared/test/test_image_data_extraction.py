#!/usr/bin/env python3
"""
Unit tests for image data extraction tool.
"""

import unittest
import sys
import os
import json
from unittest.mock import patch, MagicMock, AsyncMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestImageDataExtractionConfig(unittest.TestCase):
    """Test create_image_data_extraction_tools_from_config."""

    def test_returns_empty_for_no_config(self):
        from shared.utils.tools.image_tools import create_image_data_extraction_tools_from_config
        result = create_image_data_extraction_tools_from_config({})
        self.assertEqual(result, [])

    def test_returns_empty_for_missing_image_data_extraction(self):
        from shared.utils.tools.image_tools import create_image_data_extraction_tools_from_config
        config = {"tool_config": '{"image_tools": true}'}
        result = create_image_data_extraction_tools_from_config(config)
        self.assertEqual(result, [])

    def test_boolean_true_creates_default_tool(self):
        from shared.utils.tools.image_tools import create_image_data_extraction_tools_from_config
        config = {"tool_config": '{"image_data_extraction": true}', "name": "test_agent"}
        result = create_image_data_extraction_tools_from_config(config)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].__name__, "extract_data_from_image")

    def test_dict_config_creates_configured_tool(self):
        from shared.utils.tools.image_tools import create_image_data_extraction_tools_from_config
        config = {
            "tool_config": '{"image_data_extraction": {"model": "openai/gpt-4o"}}',
            "name": "test_agent"
        }
        result = create_image_data_extraction_tools_from_config(config)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].__name__, "extract_data_from_image")

    def test_invalid_json_returns_empty(self):
        from shared.utils.tools.image_tools import create_image_data_extraction_tools_from_config
        config = {"tool_config": "not valid json", "name": "test_agent"}
        result = create_image_data_extraction_tools_from_config(config)
        self.assertEqual(result, [])

    def test_boolean_false_returns_empty(self):
        from shared.utils.tools.image_tools import create_image_data_extraction_tools_from_config
        config = {"tool_config": '{"image_data_extraction": false}', "name": "test_agent"}
        result = create_image_data_extraction_tools_from_config(config)
        self.assertEqual(result, [])


class TestToolFactoryRegistration(unittest.TestCase):
    """Test that image_data_extraction is registered in ToolFactory."""

    def test_image_data_extraction_in_tool_types(self):
        from shared.utils.tools.tool_factory import ToolFactory
        factory = ToolFactory()
        self.assertIn("image_data_extraction", factory.get_available_tool_types())

    def test_factory_has_creator_method(self):
        from shared.utils.tools.tool_factory import ToolFactory
        factory = ToolFactory()
        self.assertTrue(hasattr(factory, "_create_image_data_extraction_tools"))
        self.assertTrue(callable(factory._create_image_data_extraction_tools))


class TestExtractDataFromImage(unittest.IsolatedAsyncioTestCase):
    """Test extract_data_from_image function."""

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}, clear=False)
    @patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
    async def test_missing_api_key(self):
        from shared.utils.tools.image_tools import extract_data_from_image
        # Clear both keys to trigger error
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": ""}, clear=False):
            result = await extract_data_from_image("https://example.com/image.png")
            self.assertFalse(result["success"])
            self.assertEqual(result["error_type"], "missing_api_key")

    @patch("shared.utils.tools.image_tools.OpenAI")
    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False)
    async def test_successful_extraction(self, mock_openai_class):
        from shared.utils.tools.image_tools import extract_data_from_image
        # Mock the OpenAI client
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 100
        mock_usage.completion_tokens = 50
        mock_usage.total_tokens = 150

        mock_message = MagicMock()
        mock_message.content = "Extracted text from image: Hello World"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_client.chat.completions.create.return_value = mock_response

        result = await extract_data_from_image(
            "https://example.com/image.png",
            "Extract all text"
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["extracted_data"], "Extracted text from image: Hello World")
        self.assertEqual(result["image_url"], "https://example.com/image.png")
        self.assertIn("usage", result)
        self.assertEqual(result["usage"]["total_tokens"], 150)

    @patch("shared.utils.tools.image_tools.OpenAI")
    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False)
    async def test_api_error_handling(self, mock_openai_class):
        from shared.utils.tools.image_tools import extract_data_from_image
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("Rate limit exceeded")

        result = await extract_data_from_image("https://example.com/image.png")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "rate_limit_error")
        self.assertIn("Rate limit", result["error"])


class TestAgentTemplate(unittest.TestCase):
    """Test the image-data-extractor template is valid JSON."""

    def test_template_loads(self):
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "templates", "agent_templates", "image-data-extractor.json"
        )
        with open(template_path) as f:
            data = json.load(f)
        self.assertEqual(data["template_meta"]["id"], "image-data-extractor")
        self.assertEqual(data["template_meta"]["root_agent"], "image_data_extractor")
        self.assertEqual(len(data["agents"]), 1)
        agent = data["agents"][0]
        self.assertEqual(agent["name"], "image_data_extractor")
        tool_cfg = json.loads(agent["tool_config"])
        self.assertIn("image_data_extraction", tool_cfg)


if __name__ == "__main__":
    unittest.main()
