"""Tests for backend-specific database connection factories."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import connect_sqlite


class SQLiteConnectionFactoryTests(unittest.TestCase):
    def test_connection_returns_named_rows_and_enforces_foreign_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect_sqlite(Path(directory) / "connection-test.sqlite3")
            try:
                connection.execute("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
                connection.execute(
                    "CREATE TABLE children (parent_id INTEGER REFERENCES parents(id))"
                )
                row = connection.execute("SELECT 7 AS answer").fetchone()
                self.assertIsInstance(row, sqlite3.Row)
                self.assertEqual(row["answer"], 7)
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO children (parent_id) VALUES (999)")
            finally:
                connection.close()

    def test_timeout_is_forwarded_to_sqlite_driver(self):
        connection = connect_sqlite(":memory:", timeout=3.5)
        try:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
