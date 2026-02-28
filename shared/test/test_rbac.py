#!/usr/bin/env python3
"""
Unit tests for RBAC middleware and AccessDeniedException.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.rbac_middleware import RBACMiddleware, AccessDeniedException, get_rbac_middleware


class TestAccessDeniedException(unittest.TestCase):

    def test_basic_exception(self):
        exc = AccessDeniedException("denied")
        self.assertEqual(str(exc), "denied")
        self.assertEqual(exc.message, "denied")
        self.assertEqual(exc.required_roles, [])
        self.assertEqual(exc.user_roles, [])

    def test_exception_with_roles(self):
        exc = AccessDeniedException(
            "no access",
            required_roles=["admin"],
            user_roles=["user"]
        )
        self.assertEqual(exc.required_roles, ["admin"])
        self.assertEqual(exc.user_roles, ["user"])

    def test_is_exception(self):
        exc = AccessDeniedException("test")
        self.assertIsInstance(exc, Exception)

    def test_can_be_raised_and_caught(self):
        with self.assertRaises(AccessDeniedException) as ctx:
            raise AccessDeniedException("forbidden", required_roles=["admin"])
        self.assertEqual(ctx.exception.required_roles, ["admin"])


class TestRBACMiddleware(unittest.TestCase):

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_no_role_restrictions_allows_access(self, mock_get_service):
        mock_service = Mock()
        mock_user = Mock()
        mock_user.get_roles.return_value = ["user"]
        mock_service.get_or_create_user.return_value = mock_user
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        has_access, error = middleware.check_agent_access("user1", {"name": "agent1", "allowed_for_roles": []})
        self.assertTrue(has_access)
        self.assertIsNone(error)

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_none_role_restrictions_allows_access(self, mock_get_service):
        mock_service = Mock()
        mock_user = Mock()
        mock_service.get_or_create_user.return_value = mock_user
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        has_access, error = middleware.check_agent_access("user1", {"name": "agent1"})
        self.assertTrue(has_access)
        self.assertIsNone(error)

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_user_with_matching_role_gets_access(self, mock_get_service):
        mock_service = Mock()
        mock_user = Mock()
        mock_user.get_roles.return_value = ["admin"]
        mock_service.get_or_create_user.return_value = mock_user
        mock_service.check_user_access.return_value = True
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        has_access, error = middleware.check_agent_access("admin_user", {"name": "admin_agent", "allowed_for_roles": ["admin"]})
        self.assertTrue(has_access)
        self.assertIsNone(error)

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_user_without_matching_role_denied(self, mock_get_service):
        mock_service = Mock()
        mock_user = Mock()
        mock_user.get_roles.return_value = ["user"]
        mock_service.get_or_create_user.return_value = mock_user
        mock_service.check_user_access.return_value = False
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        has_access, error = middleware.check_agent_access("basic_user", {"name": "admin_agent", "allowed_for_roles": ["admin"]})
        self.assertFalse(has_access)
        self.assertIn("Access denied", error)

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_failed_user_authentication(self, mock_get_service):
        mock_service = Mock()
        mock_service.get_or_create_user.return_value = None
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        has_access, error = middleware.check_agent_access("unknown", {"name": "agent"})
        self.assertFalse(has_access)
        self.assertIn("Failed to authenticate", error)

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_exception_during_check(self, mock_get_service):
        mock_service = Mock()
        mock_service.get_or_create_user.side_effect = RuntimeError("DB down")
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        has_access, error = middleware.check_agent_access("user1", {"name": "agent"})
        self.assertFalse(has_access)
        self.assertIn("RBAC check failed", error)

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_get_user_roles(self, mock_get_service):
        mock_service = Mock()
        mock_service.get_user_roles.return_value = ["admin", "user"]
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        roles = middleware.get_user_roles("user1")
        self.assertEqual(roles, ["admin", "user"])

    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_update_user_roles(self, mock_get_service):
        mock_service = Mock()
        mock_service.update_user_roles.return_value = True
        mock_get_service.return_value = mock_service

        middleware = RBACMiddleware()
        result = middleware.update_user_roles("user1", ["admin"])
        self.assertTrue(result)
        mock_service.update_user_roles.assert_called_once_with("user1", ["admin"])


class TestGetRBACMiddleware(unittest.TestCase):

    @patch('shared.utils.rbac_middleware._rbac_middleware', None)
    @patch('shared.utils.rbac_middleware.get_user_service')
    def test_singleton_creation(self, mock_get_service):
        import shared.utils.rbac_middleware as rbac_mod
        rbac_mod._rbac_middleware = None
        mock_get_service.return_value = Mock()
        
        middleware = get_rbac_middleware()
        self.assertIsInstance(middleware, RBACMiddleware)


if __name__ == '__main__':
    unittest.main()
