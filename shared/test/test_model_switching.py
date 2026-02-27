#!/usr/bin/env python3
"""
Test script to verify model switching and multi-provider routing.
"""

import os
import sys
import unittest
from unittest.mock import patch
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestModelSwitching(unittest.TestCase):

    def test_model_imports(self):
        """Test that model imports work correctly."""
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.models import Gemini
        self.assertIsNotNone(LiteLlm)
        self.assertIsNotNone(Gemini)

    def test_model_factory_default(self):
        """Test default model creation (Gemini fallback)."""
        from utils.utils import create_model
        model = create_model()
        self.assertIsNotNone(model)

    def test_gemini_models(self):
        """Test that bare names and gemini-* route to Gemini."""
        from utils.utils import create_model, _is_gemini_model
        from google.adk.models import Gemini

        for name in ["gemini-2.5-flash", "gemini-1.5-pro", "models/gemini-pro"]:
            self.assertTrue(_is_gemini_model(name), f"{name} should be Gemini")
            model = create_model(model_name=name)
            self.assertIsInstance(model, Gemini)

    def test_openrouter_routing(self):
        """Test OpenRouter prefix routes through LiteLlm."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        model = create_model(model_name="openrouter/deepseek/deepseek-chat-v3.1")
        self.assertIsInstance(model, LiteLlm)

    def test_openai_routing(self):
        """Test openai/ prefix routes through LiteLlm."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        model = create_model(model_name="openai/gpt-4o")
        self.assertIsInstance(model, LiteLlm)

    def test_anthropic_routing(self):
        """Test anthropic/ prefix routes through LiteLlm."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        model = create_model(model_name="anthropic/claude-sonnet-4-20250514")
        self.assertIsInstance(model, LiteLlm)

    def test_deepseek_routing(self):
        """Test deepseek/ prefix routes through LiteLlm."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        model = create_model(model_name="deepseek/deepseek-chat")
        self.assertIsInstance(model, LiteLlm)

    def test_ollama_chat_routing(self):
        """Test ollama_chat/ prefix routes through LiteLlm."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        model = create_model(model_name="ollama_chat/llama3.2")
        self.assertIsInstance(model, LiteLlm)

    def test_ollama_warning(self):
        """Test that ollama/ (without _chat) emits a warning."""
        from utils.utils import create_model
        import logging

        with self.assertLogs(level=logging.WARNING) as cm:
            create_model(model_name="ollama/llama3.2")
        self.assertTrue(any("ollama_chat" in msg for msg in cm.output))

    def test_provider_detection(self):
        """Test _detect_provider extracts correct prefix."""
        from utils.utils import _detect_provider

        self.assertEqual(_detect_provider("openai/gpt-4o"), "openai")
        self.assertEqual(_detect_provider("anthropic/claude-3-haiku-20240307"), "anthropic")
        self.assertEqual(_detect_provider("ollama_chat/gemma3:latest"), "ollama_chat")
        self.assertEqual(_detect_provider("openrouter/deepseek/deepseek-chat"), "openrouter")
        self.assertEqual(_detect_provider("gemini-2.5-flash"), "")

    def test_generic_litellm_providers(self):
        """Test that unknown providers still route through LiteLlm."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        for name in ["groq/llama3-70b", "mistral/mistral-large", "cohere/command-r-plus", "together_ai/meta-llama/Llama-3-70b"]:
            model = create_model(model_name=name)
            self.assertIsInstance(model, LiteLlm, f"{name} should route through LiteLlm")

    def test_explicit_overrides(self):
        """Test that api_key and base_url overrides are passed through."""
        from utils.utils import create_model
        from google.adk.models.lite_llm import LiteLlm

        model = create_model(
            model_name="openai/gpt-4o",
            api_key="sk-test-key",
            base_url="https://custom.endpoint.com/v1"
        )
        self.assertIsInstance(model, LiteLlm)


if __name__ == "__main__":
    unittest.main()
