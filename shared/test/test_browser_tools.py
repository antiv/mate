#!/usr/bin/env python3
"""
Unit tests for Playwright-based browser tools.
"""

import os
import sys
import shutil
import unittest
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.tools.browser_tools import (
    browser_manager,
    browser_navigate,
    browser_screenshot,
    browser_get_html,
    _get_context_values,
    _generate_page_summary
)


class TestBrowserTools(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Setup clean profiles directory for testing
        self.test_profiles_dir = os.path.abspath("data/browser_profiles")
        if os.path.exists(self.test_profiles_dir):
            try:
                shutil.rmtree(self.test_profiles_dir)
            except Exception:
                pass

    async def asyncTearDown(self):
        # Close all active test sessions
        for key in list(browser_manager._sessions.keys()):
            try:
                await browser_manager.close_session(key[0], key[1])
            except Exception:
                pass
                
        # Clean up test directories
        if os.path.exists(self.test_profiles_dir):
            try:
                shutil.rmtree(self.test_profiles_dir)
            except Exception:
                pass

    async def test_session_directory_isolation(self):
        """Verify that different user IDs get separate profile directories."""
        session_a = await browser_manager.get_session("userA", "session1")
        session_b = await browser_manager.get_session("userB", "session1")
        
        self.assertNotEqual(session_a.profile_dir, session_b.profile_dir)
        self.assertTrue(session_a.profile_dir.endswith("user_userA"))
        self.assertTrue(session_b.profile_dir.endswith("user_userB"))

    async def test_session_lifecycle(self):
        """Verify that browser sessions can be opened, cached, and closed."""
        session_key = ("user_test", "session_test")
        
        # Get session creates it
        session = await browser_manager.get_session(*session_key)
        self.assertIn(session_key, browser_manager._sessions)
        
        # Second call returns the same cached instance
        session_cached = await browser_manager.get_session(*session_key)
        self.assertEqual(session, session_cached)
        
        # Closing the session removes it from manager
        await browser_manager.close_session(*session_key)
        self.assertNotIn(session_key, browser_manager._sessions)

    def test_get_context_values(self):
        """Test ToolContext property extraction."""
        # Test default fallback when context is None
        app, user, sess = _get_context_values(None)
        self.assertEqual(app, "unknown")
        self.assertEqual(user, "default")
        self.assertEqual(sess, "default")

        # Test context with mock properties
        mock_context = MagicMock()
        mock_context._invocation_context = None
        mock_context.app_name = "test_app"
        mock_context.user_id = "test_user"
        mock_context.session_id = "test_sess"
        
        app, user, sess = _get_context_values(mock_context)
        self.assertEqual(app, "test_app")
        self.assertEqual(user, "test_user")
        self.assertEqual(sess, "test_sess")

    async def test_generate_page_summary(self):
        """Test summary parsing from HTML."""
        mock_page = MagicMock()
        mock_page.title = AsyncMock(return_value="Example Page")
        mock_page.url = "https://example.com"
        mock_page.evaluate = AsyncMock(return_value="Hello World\nThis is a test page.")
        
        summary = await _generate_page_summary(mock_page, None)
        self.assertIn("Title: Example Page", summary)
        self.assertIn("URL: https://example.com", summary)
        self.assertIn("Hello World", summary)
        self.assertIn("This is a test page.", summary)

    async def test_real_headless_navigation(self):
        """Run a lightweight real headless navigation to verify Playwright operation."""
        # Skip if no chromium is installed or running in strict sandbox
        try:
            import playwright
        except ImportError:
            self.skipTest("Playwright library not available")

        # Configure headless run explicitly
        os.environ["BROWSER_HEADLESS"] = "true"
        os.environ["BROWSER_MODE"] = "headless"
        
        mock_context = MagicMock()
        mock_context._invocation_context = None
        mock_context.app_name = "test_suite"
        mock_context.user_id = "test_user_nav"
        mock_context.session_id = "test_sess_nav"
        mock_context.save_artifact = AsyncMock(return_value="v1")
        
        # Test navigation to a local file or example page
        result = await browser_navigate("https://example.com", mock_context)
        self.assertNotIn("Error", result)
        self.assertIn("Example Domain", result)
        
        # Get active HTML
        html = await browser_get_html(mock_context)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Example Domain", html)


if __name__ == '__main__':
    unittest.main()
