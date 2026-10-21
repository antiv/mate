#!/usr/bin/env python3
"""
get_request_context: the caller of a dashboard route, in one place (#276, item 2).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server.request_context import RequestContext, get_request_context


class TestRequestContext(unittest.TestCase):

    def test_carries_the_signed_in_user(self):
        with patch("server.request_context.require_dashboard_auth", return_value="alice"):
            ctx = get_request_context(MagicMock())
        self.assertEqual(ctx.user_id, "alice")

    def test_signed_out_caller_is_sent_to_login(self):
        redirect = HTTPException(status_code=307, headers={"Location": "/login"})
        with patch("server.request_context.require_dashboard_auth", side_effect=redirect):
            with self.assertRaises(HTTPException) as raised:
                get_request_context(MagicMock())
        self.assertEqual(raised.exception.status_code, 307)

    def test_role_and_is_admin_come_from_get_user_role(self):
        for role, is_admin in (("admin", True), ("user", False)):
            with patch("server.request_context.get_user_role", return_value=role):
                ctx = RequestContext(MagicMock(), "alice")
                self.assertEqual(ctx.role, role)
                self.assertIs(ctx.is_admin, is_admin)

    def test_role_is_read_once_and_only_when_asked(self):
        # For a single sign-on user the role is a database lookup
        with patch("server.request_context.get_user_role", return_value="user") as get_role:
            ctx = RequestContext(MagicMock(), "alice")
            get_role.assert_not_called()
            ctx.is_admin
            ctx.role
        get_role.assert_called_once()


if __name__ == "__main__":
    unittest.main()
