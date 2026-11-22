#!/usr/bin/env python3
"""
ToolFactory and custom tool tests without complex mocking.
"""

import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.tools.tool_factory import ToolFactory
from shared.utils.tools.custom_tools import create_custom_function_tools


class TestCoreFunctionality(unittest.TestCase):
    
    def test_tool_factory_require_confirmation_wrapping(self):
        """Test that tools listed in require_confirmation get wrapped in FunctionTool."""
        import json
        from google.adk.tools.function_tool import FunctionTool

        factory = ToolFactory()
        config = {
            'name': 'test_agent',
            'tool_config': json.dumps({'require_confirmation': ['update_user_profile']})
        }
        result = factory.create_tools(config)

        wrapped = [t for t in result if isinstance(t, FunctionTool) and t.name == 'update_user_profile']
        self.assertEqual(len(wrapped), 1)
        self.assertTrue(wrapped[0]._require_confirmation)
        # Tools not listed stay unwrapped
        unwrapped_names = [getattr(t, '__name__', None) for t in result if not isinstance(t, FunctionTool)]
        self.assertIn('get_user_profile', unwrapped_names)

    def test_tool_factory_require_confirmation_absent(self):
        """Test that without require_confirmation config no FunctionTool wrapping happens."""
        import json
        from google.adk.tools.function_tool import FunctionTool

        factory = ToolFactory()
        config = {
            'name': 'test_agent',
            'tool_config': json.dumps({})
        }
        result = factory.create_tools(config)
        self.assertFalse(any(isinstance(t, FunctionTool) for t in result))

    def test_tool_factory_invalid_config(self):
        """Test ToolFactory with invalid configuration."""
        factory = ToolFactory()

        # Invalid JSON in tool_config should be handled gracefully
        config = {
            'tool_config': 'invalid json string',
            'agent_name': 'test_agent'
        }
        
        result = factory.create_tools(config)
        # User profile tools are automatically added even with invalid config
        self.assertGreater(len(result), 0)
        # Check that user profile tools are present
        tool_names = [getattr(t, "__name__", str(t)) for t in result]
        self.assertIn("update_user_profile", tool_names)
        self.assertIn("get_user_profile", tool_names)
    
    def test_custom_tools_empty_list(self):
        """Test custom tools creation with empty function list."""
        result = create_custom_function_tools([], 'test_agent')
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
