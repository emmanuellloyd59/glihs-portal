"""Tests for the isolated Psycopg connection primitives."""
import unittest
from unittest.mock import patch

from database import DatabaseConfigurationError
from db.postgresql import HybridRow, connect_postgresql, hybrid_dict_row


class FakeColumn:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    description = [FakeColumn("id"), FakeColumn("username")]


class PostgreSQLRowFactoryTests(unittest.TestCase):
    def test_row_supports_named_and_positional_access(self):
        make_row = hybrid_dict_row(FakeCursor())
        row = make_row((17, "teacher"))
        self.assertIsInstance(row, HybridRow)
        self.assertEqual(row["id"], 17)
        self.assertEqual(row[0], 17)
        self.assertEqual(row["username"], "teacher")
        self.assertEqual(row[1], "teacher")

    def test_invalid_url_is_rejected_before_driver_call(self):
        with self.assertRaises(DatabaseConfigurationError):
            connect_postgresql("sqlite:///not-postgres.db")

    def test_timeout_must_be_positive(self):
        with self.assertRaises(ValueError):
            connect_postgresql("postgresql://example", timeout=0)

    def test_factory_uses_psycopg_with_hybrid_row_factory(self):
        import psycopg
        sentinel = object()
        with patch.object(psycopg, "connect", return_value=sentinel) as connect:
            result = connect_postgresql("postgresql://example/db", timeout=3.8)
        self.assertIs(result, sentinel)
        connect.assert_called_once_with(
            "postgresql://example/db",
            connect_timeout=3,
            row_factory=hybrid_dict_row,
        )



class PostgreSQLServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        cls.database_url = os.environ.get("TEST_POSTGRES_URL")
        if not cls.database_url:
            raise unittest.SkipTest("TEST_POSTGRES_URL is not configured")

    def test_real_server_connection_row_access_and_transaction(self):
        connection = connect_postgresql(self.database_url)
        try:
            row = connection.execute(
                "SELECT 42 AS answer, 'connected' AS state"
            ).fetchone()
            self.assertEqual(row["answer"], 42)
            self.assertEqual(row[0], 42)
            self.assertEqual(row["state"], "connected")
            self.assertEqual(row[1], "connected")

            connection.execute(
                "CREATE TEMP TABLE cmhs_connection_probe (id INTEGER PRIMARY KEY)"
            )
            connection.execute(
                "INSERT INTO cmhs_connection_probe (id) VALUES (%s)", (1,)
            )
            connection.rollback()
            tables = connection.execute(
                "SELECT to_regclass('pg_temp.cmhs_connection_probe')"
            ).fetchone()
            self.assertIsNone(tables[0])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
