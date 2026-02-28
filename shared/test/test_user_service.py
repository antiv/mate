#!/usr/bin/env python3
"""
Unit tests for the UserService.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.user_service import UserService
from shared.utils.models import User


class TestUserServiceInit(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_initialization(self, mock_get_db):
        mock_get_db.return_value = Mock()
        service = UserService()
        self.assertIsNotNone(service.db_client)

    @patch('shared.utils.user_service.get_database_client')
    def test_get_session(self, mock_get_db):
        mock_client = Mock()
        mock_session = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        session = service.get_session()
        self.assertEqual(session, mock_session)

    @patch('shared.utils.user_service.get_database_client')
    def test_get_session_returns_none(self, mock_get_db):
        mock_client = Mock()
        mock_client.get_session.return_value = None
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertIsNone(service.get_session())


class TestGetOrCreateUser(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_existing_user(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_user.user_id = "existing_user"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_or_create_user("existing_user")
        self.assertEqual(result, mock_user)
        mock_session.close.assert_called_once()

    @patch('shared.utils.user_service.get_database_client')
    def test_creates_new_user(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_or_create_user("new_user")
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_none_on_no_session(self, mock_get_db):
        mock_client = Mock()
        mock_client.get_session.return_value = None
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_or_create_user("user1")
        self.assertIsNone(result)

    @patch('shared.utils.user_service.get_database_client')
    def test_handles_general_exception(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.side_effect = RuntimeError("DB error")

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_or_create_user("user1")
        self.assertIsNone(result)
        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


class TestGetUserById(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_user(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_user_by_id("user1")
        self.assertEqual(result, mock_user)
        mock_session.close.assert_called_once()

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_none_for_missing_user(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_user_by_id("nonexistent")
        self.assertIsNone(result)

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_none_on_error(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.side_effect = RuntimeError("DB error")

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.get_user_by_id("user1")
        self.assertIsNone(result)


class TestUpdateUserRoles(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_update_success(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.update_user_roles("user1", ["admin", "user"])
        self.assertTrue(result)
        mock_user.set_roles.assert_called_once_with(["admin", "user"])
        mock_session.commit.assert_called_once()

    @patch('shared.utils.user_service.get_database_client')
    def test_update_user_not_found(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.update_user_roles("nonexistent", ["admin"])
        self.assertFalse(result)

    @patch('shared.utils.user_service.get_database_client')
    def test_update_on_error(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.side_effect = RuntimeError("error")

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.update_user_roles("user1", ["admin"])
        self.assertFalse(result)
        mock_session.rollback.assert_called_once()


class TestCheckUserAccess(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_empty_roles_allows_access(self, mock_get_db):
        mock_get_db.return_value = Mock()
        service = UserService()
        self.assertTrue(service.check_user_access("user1", []))

    @patch('shared.utils.user_service.get_database_client')
    def test_matching_role_grants_access(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_user.get_roles.return_value = ["admin", "user"]
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertTrue(service.check_user_access("user1", ["admin"]))

    @patch('shared.utils.user_service.get_database_client')
    def test_no_matching_role_denies_access(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_user.get_roles.return_value = ["user"]
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertFalse(service.check_user_access("user1", ["admin", "premium"]))

    @patch('shared.utils.user_service.get_database_client')
    def test_user_not_found_denies_access(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertFalse(service.check_user_access("unknown", ["admin"]))


class TestGetUserRoles(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_roles(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_user.get_roles.return_value = ["admin"]
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertEqual(service.get_user_roles("user1"), ["admin"])

    @patch('shared.utils.user_service.get_database_client')
    def test_returns_default_role_for_missing_user(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertEqual(service.get_user_roles("unknown"), ["user"])


class TestUserProfile(unittest.TestCase):

    @patch('shared.utils.user_service.get_database_client')
    def test_get_profile(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_user.get_profile_data.return_value = "profile data"
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertEqual(service.get_user_profile("user1"), "profile data")

    @patch('shared.utils.user_service.get_database_client')
    def test_get_profile_no_user(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        self.assertIsNone(service.get_user_profile("unknown"))

    @patch('shared.utils.user_service.get_database_client')
    def test_update_profile_success(self, mock_get_db):
        mock_session = Mock()
        mock_user = Mock(spec=User)
        mock_session.query.return_value.filter.return_value.first.return_value = mock_user

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.update_user_profile("user1", "new profile")
        self.assertTrue(result)
        mock_user.set_profile_data.assert_called_once_with("new profile")
        mock_session.commit.assert_called_once()

    @patch('shared.utils.user_service.get_database_client')
    def test_update_profile_user_not_found(self, mock_get_db):
        mock_session = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_client = Mock()
        mock_client.get_session.return_value = mock_session
        mock_get_db.return_value = mock_client

        service = UserService()
        result = service.update_user_profile("unknown", "data")
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
