#!/usr/bin/env python3
"""
The Supabase storage tools share a bucket with the Supabase artifact service,
which keeps every user's chat artifacts under public/. An agent steered by a chat
message must not read, overwrite or delete them through the tools.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.tools import supabase_tools as st


class TestToolPath(unittest.TestCase):

    def test_ordinary_paths_pass(self):
        self.assertEqual(st._tool_path("reports/2026/q3.pdf"), "reports/2026/q3.pdf")
        self.assertEqual(st._tool_path("/reports/./q3.pdf"), "reports/q3.pdf")

    def test_the_artifact_area_and_escapes_are_refused(self):
        for path in ("public/app/u1/user/notes.txt/0", "/public/x", "./public/x",
                     "reports/../public/x", "../x", "", "   ", ".", "public"):
            with self.assertRaises(ValueError, msg=path):
                st._tool_path(path)


class TestToolsRefuseArtifactPaths(unittest.TestCase):

    def test_no_storage_call_for_an_artifact_path(self):
        client = MagicMock()
        with patch.object(st, "_get_supabase_client", return_value=client):
            for result in (st.supabase_upload_file("public/a/u/f/0", "aGk="),
                           st.supabase_get_file("public/a/u/f/0"),
                           st.supabase_delete_file("public/a/u/f/0")):
                self.assertEqual(result["status"], "error")
                self.assertIn("public/", result["error_message"])
        client.storage.from_.assert_not_called()

    def test_an_allowed_path_reaches_storage_normalized(self):
        client = MagicMock()
        with patch.object(st, "_get_supabase_client", return_value=client):
            result = st.supabase_delete_file("/docs/./old.txt")
        self.assertEqual(result["status"], "success")
        client.storage.from_.return_value.remove.assert_called_once_with(["docs/old.txt"])


if __name__ == "__main__":
    unittest.main()
