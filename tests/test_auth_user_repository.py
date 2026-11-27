"""Cross-backend contract tests for the reviewed authentication user queries."""
import os
import sqlite3
import unittest

from db.connection import DatabaseConnection
from db.users import get_active_user_by_id, get_active_user_by_username, user_row_to_mapping


class AuthenticationUserRepositoryTests(unittest.TestCase):
    def setUp(self):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        self.connection = DatabaseConnection(raw, "sqlite")
        self.connection.execute(
            "CREATE TABLE schools (id INTEGER PRIMARY KEY, status TEXT NOT NULL)"
        )
        self.connection.execute(
            """CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                password_hash TEXT NOT NULL, role TEXT NOT NULL,
                is_super_admin INTEGER NOT NULL DEFAULT 0,
                school_id INTEGER, status TEXT NOT NULL
            )"""
        )
        self.connection.execute("INSERT INTO schools VALUES (1, 'active')")
        self.connection.execute("INSERT INTO schools VALUES (2, 'suspended')")
        self.connection.execute(
            "INSERT INTO users VALUES (1,'teacher','hash-t','teacher',0,1,'active')"
        )
        self.connection.execute(
            "INSERT INTO users VALUES (2,'inactive_school','hash-i','teacher',0,2,'active')"
        )
        self.connection.execute(
            "INSERT INTO users VALUES (3,'suspended_user','hash-s','teacher',0,1,'suspended')"
        )
        self.connection.execute(
            "INSERT INTO users VALUES (4,'root','hash-r','super_admin',1,NULL,'active')"
        )
        self.connection.execute(
            "INSERT INTO users VALUES (5,'bad_root','hash-b','super_admin',0,NULL,'active')"
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def test_active_school_user_can_be_loaded_by_id_and_username(self):
        by_id = user_row_to_mapping(get_active_user_by_id(self.connection, 1))
        by_name = user_row_to_mapping(get_active_user_by_username(self.connection, "teacher"))
        self.assertEqual(by_id, by_name)
        self.assertEqual(by_id["password_hash"], "hash-t")
        self.assertEqual(by_id["school_id"], 1)

    def test_valid_platform_super_admin_can_be_loaded_without_school(self):
        row = user_row_to_mapping(get_active_user_by_username(self.connection, "root"))
        self.assertEqual(row["role"], "super_admin")
        self.assertEqual(row["is_super_admin"], 1)
        self.assertIsNone(row["school_id"])

    def test_inactive_user_and_inactive_school_are_rejected(self):
        self.assertIsNone(get_active_user_by_id(self.connection, 3))
        self.assertIsNone(get_active_user_by_id(self.connection, 2))

    def test_malformed_super_admin_is_rejected(self):
        self.assertIsNone(get_active_user_by_username(self.connection, "bad_root"))

    def test_user_values_are_bound_not_interpolated(self):
        self.assertIsNone(
            get_active_user_by_username(self.connection, "' OR 1=1 --")
        )

    def test_none_lookup_returns_no_user(self):
        self.assertIsNone(get_active_user_by_id(self.connection, None))
        self.assertIsNone(get_active_user_by_username(self.connection, None))

    def test_row_normalizer_rejects_unmapped_tuple_rows(self):
        with self.assertRaisesRegex(TypeError, "mapping-style"):
            user_row_to_mapping((1, "teacher"))

    @unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is not configured")
    def test_same_authentication_queries_against_postgresql(self):
        from db.postgresql import connect_postgresql

        raw = connect_postgresql(os.environ["TEST_POSTGRES_URL"])
        connection = DatabaseConnection(raw, "postgresql")
        try:
            connection.execute(
                "CREATE TEMP TABLE schools (id BIGINT PRIMARY KEY, status TEXT NOT NULL)"
            )
            connection.execute(
                """CREATE TEMP TABLE users (
                    id BIGINT PRIMARY KEY, username TEXT NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL,
                    is_super_admin SMALLINT NOT NULL DEFAULT 0,
                    school_id BIGINT, status TEXT NOT NULL
                )"""
            )
            connection.execute("INSERT INTO schools VALUES (?, ?)", (1, "active"))
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, "teacher", "hash-t", "teacher", 0, 1, "active"),
            )
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                (2, "inactive_school", "hash-i", "teacher", 0, 2, "active"),
            )
            connection.execute("INSERT INTO schools VALUES (?, ?)", (2, "suspended"))
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)",
                (3, "root", "hash-r", "super_admin", 1, None, "active"),
            )
            connection.commit()
            row = user_row_to_mapping(get_active_user_by_username(connection, "teacher"))
            self.assertEqual(row["username"], "teacher")
            self.assertEqual(row["password_hash"], "hash-t")
            self.assertIsNone(get_active_user_by_id(connection, 2))
            self.assertEqual(
                user_row_to_mapping(get_active_user_by_id(connection, 3))["role"],
                "super_admin",
            )
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
