#!/usr/bin/env python3
import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.dashboard.dashboard_server import sanitize_agent_text


class TestSanitization(unittest.TestCase):

    def test_sanitize_escaped_quotes(self):
        # Escaped single quotes
        self.assertEqual(sanitize_agent_text("don\\'t"), "don't")
        self.assertEqual(sanitize_agent_text("don\\\\'t"), "don't")
        self.assertEqual(sanitize_agent_text("no one\\'s goals"), "no one's goals")
        self.assertEqual(sanitize_agent_text("mission\\\\'s reason"), "mission's reason")
        
        # Escaped double quotes
        self.assertEqual(sanitize_agent_text('Call \\"get_users\\"'), 'Call "get_users"')
        self.assertEqual(sanitize_agent_text('Call \\\\"get_users\\\\"'), 'Call "get_users"')

    def test_sanitize_unicode_characters(self):
        # Arrows
        self.assertEqual(sanitize_agent_text("due_today → send_mission_email"), "due_today -> send_mission_email")
        self.assertEqual(sanitize_agent_text("left ← arrow"), "left <- arrow")
        
        # Em-dashes and En-dashes
        self.assertEqual(sanitize_agent_text("Heartbeat Agent — proactive"), "Heartbeat Agent - proactive")
        self.assertEqual(sanitize_agent_text("range 1–10"), "range 1-10")
        
        # Ellipsis
        self.assertEqual(sanitize_agent_text("loading…"), "loading...")
        
        # Curly quotes
        self.assertEqual(sanitize_agent_text("‘quoted’"), "'quoted'")
        self.assertEqual(sanitize_agent_text("“quoted”"), '"quoted"')

    def test_sanitize_none_and_empty(self):
        self.assertIsNone(sanitize_agent_text(None))
        self.assertEqual(sanitize_agent_text(""), "")
        self.assertEqual(sanitize_agent_text("  "), "  ")


if __name__ == '__main__':
    unittest.main()
