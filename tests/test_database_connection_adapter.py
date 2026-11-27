"""Contract tests for the staged DB-API connection facade."""
import sqlite3
import unittest

from db.connection import DatabaseConnection


class RecordingConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, parameters=()):
        self.calls.append(("execute", sql, parameters))
        return "cursor"

    def executemany(self, sql, parameter_sets):
        rows = list(parameter_sets)
        self.calls.append(("executemany", sql, rows))
        return "batch-cursor"

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class DatabaseConnectionAdapterTests(unittest.TestCase):
    def test_sqlite_execution_is_passed_through_unchanged(self):
        raw = RecordingConnection()
        db = DatabaseConnection(raw, "sqlite")
        result = db.execute("SELECT ? AS value", (9,))
        self.assertEqual(result, "cursor")
        self.assertEqual(raw.calls, [("execute", "SELECT ? AS value", (9,))])

    def test_postgresql_execution_converts_only_bind_markers(self):
        raw = RecordingConnection()
        db = DatabaseConnection(raw, "postgresql")
        db.execute("SELECT '?' AS literal, ? AS value -- ? ignored", (9,))
        self.assertEqual(
            raw.calls,
            [("execute", "SELECT '?' AS literal, %s AS value -- ? ignored", (9,))],
        )

    def test_postgresql_executemany_converts_and_validates_rows(self):
        raw = RecordingConnection()
        db = DatabaseConnection(raw, "postgresql")
        db.executemany(
            "INSERT INTO example (a, b) VALUES (?, ?)",
            [(1, "one"), (2, "two")],
        )
        self.assertEqual(
            raw.calls,
            [("executemany", "INSERT INTO example (a, b) VALUES (%s, %s)",
              [(1, "one"), (2, "two")])],
        )

    def test_postgresql_executemany_rejects_inconsistent_parameter_lengths(self):
        db = DatabaseConnection(RecordingConnection(), "postgresql")
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            db.executemany("INSERT INTO example VALUES (?, ?)", [(1, 2), (3,)])

    def test_postgresql_executemany_rejects_empty_input_explicitly(self):
        db = DatabaseConnection(RecordingConnection(), "postgresql")
        with self.assertRaisesRegex(ValueError, "at least one"):
            db.executemany("INSERT INTO example VALUES (?)", [])

    def test_postgresql_rejects_named_parameter_mappings_until_supported(self):
        db = DatabaseConnection(RecordingConnection(), "postgresql")
        with self.assertRaisesRegex(TypeError, "positional parameters"):
            db.execute("SELECT %(value)s", {"value": 1})

    def test_lifecycle_methods_delegate_to_driver(self):
        raw = RecordingConnection()
        db = DatabaseConnection(raw, "sqlite")
        db.commit()
        db.rollback()
        db.close()
        self.assertEqual(raw.commits, 1)
        self.assertEqual(raw.rollbacks, 1)
        self.assertTrue(raw.closed)

    def test_sqlite_facade_preserves_row_and_transaction_behavior(self):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        db = DatabaseConnection(raw, "sqlite")
        try:
            db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO sample (value) VALUES (?)", ("kept",))
            row = db.execute("SELECT id, value FROM sample").fetchone()
            self.assertEqual(row["value"], "kept")
            self.assertEqual(row[0], 1)
            self.assertTrue(db.in_transaction)
            db.rollback()
            self.assertFalse(db.in_transaction)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 0)
        finally:
            db.close()

    def test_unknown_backend_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "backend"):
            DatabaseConnection(RecordingConnection(), "mysql")


if __name__ == "__main__":
    unittest.main()
