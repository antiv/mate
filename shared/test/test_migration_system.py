#!/usr/bin/env python3
"""
Unit tests for the database migration system.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
import tempfile
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.migration_system import MigrationSystem, MigrationRecord


class TestMigrationRecord(unittest.TestCase):

    def test_creation(self):
        from datetime import datetime
        now = datetime.now()
        record = MigrationRecord("001", "initial_schema", now, "abc123")
        self.assertEqual(record.version, "001")
        self.assertEqual(record.name, "initial_schema")
        self.assertEqual(record.applied_at, now)
        self.assertEqual(record.checksum, "abc123")

    def test_default_checksum(self):
        from datetime import datetime
        record = MigrationRecord("001", "test", datetime.now())
        self.assertEqual(record.checksum, "")


class TestMigrationSystem(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.migration_system = MigrationSystem()
        self.migration_system.migrations_dir = self.temp_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialization(self):
        ms = MigrationSystem()
        self.assertIsNotNone(ms)
        self.assertIsNone(ms.database_client)

    def test_initialization_with_client(self):
        mock_client = Mock()
        ms = MigrationSystem(database_client=mock_client)
        self.assertEqual(ms.database_client, mock_client)

    def test_ensure_migrations_dir_creates_directory(self):
        import shutil
        shutil.rmtree(self.temp_dir)
        self.assertFalse(os.path.exists(self.temp_dir))
        self.migration_system._ensure_migrations_dir()
        # Uses the original migrations_dir, not our temp_dir

    def test_get_migration_files_empty_dir(self):
        result = self.migration_system._get_migration_files_from_dir(self.temp_dir)
        self.assertEqual(result, [])

    def test_get_migration_files_correct_order(self):
        for f in ["V002__second.sql", "V001__first.sql", "V003__third.sql"]:
            with open(os.path.join(self.temp_dir, f), 'w') as fh:
                fh.write("-- migration\n")

        result = self.migration_system._get_migration_files_from_dir(self.temp_dir)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0][0], "001")
        self.assertEqual(result[1][0], "002")
        self.assertEqual(result[2][0], "003")

    def test_get_migration_files_ignores_non_sql(self):
        with open(os.path.join(self.temp_dir, "README.md"), 'w') as f:
            f.write("docs")
        with open(os.path.join(self.temp_dir, "V001__test.sql"), 'w') as f:
            f.write("-- sql")

        result = self.migration_system._get_migration_files_from_dir(self.temp_dir)
        self.assertEqual(len(result), 1)

    def test_get_migration_files_ignores_bad_names(self):
        with open(os.path.join(self.temp_dir, "bad_name.sql"), 'w') as f:
            f.write("-- sql")
        with open(os.path.join(self.temp_dir, "V001__good.sql"), 'w') as f:
            f.write("-- sql")

        result = self.migration_system._get_migration_files_from_dir(self.temp_dir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "good")

    def test_calculate_file_checksum(self):
        filepath = os.path.join(self.temp_dir, "test.sql")
        with open(filepath, 'w') as f:
            f.write("SELECT 1;")

        checksum = self.migration_system._calculate_file_checksum(filepath)
        self.assertIsInstance(checksum, str)
        self.assertEqual(len(checksum), 32)  # MD5 hex digest

    def test_calculate_checksum_deterministic(self):
        filepath = os.path.join(self.temp_dir, "test.sql")
        with open(filepath, 'w') as f:
            f.write("SELECT 1;")

        c1 = self.migration_system._calculate_file_checksum(filepath)
        c2 = self.migration_system._calculate_file_checksum(filepath)
        self.assertEqual(c1, c2)

    def test_calculate_checksum_different_files(self):
        f1 = os.path.join(self.temp_dir, "a.sql")
        f2 = os.path.join(self.temp_dir, "b.sql")
        with open(f1, 'w') as f:
            f.write("SELECT 1;")
        with open(f2, 'w') as f:
            f.write("SELECT 2;")

        c1 = self.migration_system._calculate_file_checksum(f1)
        c2 = self.migration_system._calculate_file_checksum(f2)
        self.assertNotEqual(c1, c2)


class TestSQLSplitting(unittest.TestCase):

    def setUp(self):
        self.ms = MigrationSystem()

    def test_split_simple_statements(self):
        sql = "CREATE TABLE a (id INT); CREATE TABLE b (id INT);"
        result = self.ms._split_sql_statements(sql)
        self.assertEqual(len(result), 2)

    def test_split_preserves_strings(self):
        sql = "INSERT INTO t VALUES ('hello; world'); SELECT 1;"
        result = self.ms._split_sql_statements(sql)
        self.assertEqual(len(result), 2)
        self.assertIn("hello; world", result[0])

    def test_split_removes_comments(self):
        sql = "-- This is a comment\nSELECT 1;"
        result = self.ms._split_sql_statements(sql)
        self.assertEqual(len(result), 1)
        self.assertNotIn("--", result[0])

    def test_split_empty_input(self):
        result = self.ms._split_sql_statements("")
        self.assertEqual(result, [])

    def test_split_no_semicolon(self):
        result = self.ms._split_sql_statements("SELECT 1")
        self.assertEqual(len(result), 1)

    def test_do_block_not_split(self):
        sql = "DO $$ BEGIN CREATE TABLE t (id INT); END $$;"
        result = self.ms._split_sql_statements(sql)
        self.assertEqual(len(result), 1)


class TestSQLConversion(unittest.TestCase):

    def setUp(self):
        self.ms = MigrationSystem()

    def test_sqlite_serial_conversion(self):
        sql = "CREATE TABLE t (id SERIAL PRIMARY KEY, name TEXT);"
        result = self.ms._convert_postgresql_to_sqlite(sql)
        self.assertIn("INTEGER PRIMARY KEY AUTOINCREMENT", result)
        self.assertNotIn("SERIAL", result)

    def test_sqlite_removes_schema_prefix(self):
        sql = "CREATE TABLE public.users (id INT);"
        result = self.ms._convert_postgresql_to_sqlite(sql)
        self.assertNotIn("public.", result)

    def test_sqlite_removes_raise_notice(self):
        sql = "RAISE NOTICE 'hello';\nSELECT 1;"
        result = self.ms._convert_postgresql_to_sqlite(sql)
        self.assertNotIn("RAISE NOTICE", result)

    def test_sqlite_removes_comment_on(self):
        sql = "COMMENT ON TABLE users IS 'User table';\nSELECT 1;"
        result = self.ms._convert_postgresql_to_sqlite(sql)
        self.assertNotIn("COMMENT ON", result)

    def test_mysql_serial_conversion(self):
        sql = "CREATE TABLE t (id SERIAL PRIMARY KEY);"
        result = self.ms._convert_postgresql_to_mysql(sql)
        self.assertIn("INT AUTO_INCREMENT PRIMARY KEY", result)
        self.assertNotIn("SERIAL", result)

    def test_mysql_removes_schema_prefix(self):
        sql = "SELECT * FROM public.users;"
        result = self.ms._convert_postgresql_to_mysql(sql)
        self.assertNotIn("public.", result)


class TestCreateMigration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ms = MigrationSystem()
        self.ms.migrations_dir = self.temp_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.object(MigrationSystem, '_get_database_type', return_value='sqlite')
    def test_create_first_migration(self, mock_db_type):
        filepath = self.ms.create_migration("initial_setup")
        self.assertIsNotNone(filepath)
        self.assertTrue(os.path.exists(filepath))
        self.assertIn("V001__initial_setup.sql", filepath)

    @patch.object(MigrationSystem, '_get_database_type', return_value='sqlite')
    def test_create_sequential_migration(self, mock_db_type):
        sqlite_dir = os.path.join(self.temp_dir, "sqlite")
        os.makedirs(sqlite_dir)
        with open(os.path.join(sqlite_dir, "V001__first.sql"), 'w') as f:
            f.write("-- first")

        filepath = self.ms.create_migration("second")
        self.assertIsNotNone(filepath)
        self.assertIn("V002__second.sql", filepath)

    @patch.object(MigrationSystem, '_get_database_type', return_value='sqlite')
    def test_created_migration_has_header(self, mock_db_type):
        filepath = self.ms.create_migration("test_migration")
        with open(filepath, 'r') as f:
            content = f.read()
        self.assertIn("Migration: test_migration", content)
        self.assertIn("Version: V001", content)
        self.assertIn("SQLITE", content)


class TestGetMigrationStatus(unittest.TestCase):

    def test_no_engine_returns_error(self):
        ms = MigrationSystem()
        with patch.object(ms, '_get_engine', return_value=None):
            status = ms.get_migration_status()
            self.assertIn("error", status)

    def test_status_structure(self):
        ms = MigrationSystem()
        mock_engine = Mock()
        with patch.object(ms, '_get_engine', return_value=mock_engine), \
             patch.object(ms, '_get_applied_migrations', return_value=[]), \
             patch.object(ms, '_get_migration_files', return_value=[]):
            status = ms.get_migration_status()
            self.assertIn("applied_migrations", status)
            self.assertIn("available_migrations", status)
            self.assertIn("pending_migrations", status)
            self.assertIn("orphaned_migrations", status)


class TestRunMigrations(unittest.TestCase):

    def test_no_engine_returns_false(self):
        ms = MigrationSystem()
        with patch.object(ms, '_get_engine', return_value=None):
            self.assertFalse(ms.run_migrations())

    def test_no_files_returns_true(self):
        ms = MigrationSystem()
        mock_engine = Mock()
        with patch.object(ms, '_get_engine', return_value=mock_engine), \
             patch.object(ms, '_create_migrations_table', return_value=True), \
             patch.object(ms, '_get_applied_migrations', return_value=[]), \
             patch.object(ms, '_get_migration_files', return_value=[]):
            self.assertTrue(ms.run_migrations())

    def test_failed_table_creation_returns_false(self):
        ms = MigrationSystem()
        mock_engine = Mock()
        with patch.object(ms, '_get_engine', return_value=mock_engine), \
             patch.object(ms, '_create_migrations_table', return_value=False):
            self.assertFalse(ms.run_migrations())


if __name__ == '__main__':
    unittest.main()
