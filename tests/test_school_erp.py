"""Tests for the unified school ERP configuration and safe export helpers."""
import os
import sqlite3
import tempfile
import unittest

from school_erp import (
    CORE_FEATURES,
    OPTIONAL_FEATURES,
    _csv_cell,
    initialize_school_erp_schema,
)


class SchoolERPTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)

        def connect():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db

        self.connect = connect
        db = connect()
        db.execute("CREATE TABLE schools(id INTEGER PRIMARY KEY,name TEXT NOT NULL)")
        db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO schools(id,name) VALUES(?,?)", [(1,"School A"),(2,"School B")])
        db.commit()
        db.close()
        initialize_school_erp_schema(self.connect)

    def tearDown(self):
        os.unlink(self.path)

    def test_schema_initialization_is_idempotent(self):
        initialize_school_erp_schema(self.connect)
        db = self.connect()
        try:
            count = db.execute("SELECT COUNT(*) FROM school_feature_entitlements").fetchone()[0]
            self.assertEqual(count, 2 * (len(CORE_FEATURES) + len(OPTIONAL_FEATURES)))
        finally:
            db.close()

    def test_core_features_are_enabled_for_each_school(self):
        db = self.connect()
        try:
            rows = db.execute(
                "SELECT school_id,feature_key,enabled FROM school_feature_entitlements"
            ).fetchall()
            for row in rows:
                if row["feature_key"] in CORE_FEATURES:
                    self.assertEqual(row["enabled"], 1)
                else:
                    self.assertEqual(row["enabled"], 0)
        finally:
            db.close()

    def test_entitlements_are_school_scoped(self):
        db = self.connect()
        try:
            db.execute(
                "UPDATE school_feature_entitlements SET enabled=1 WHERE school_id=1 AND feature_key='library'"
            )
            db.commit()
            a = db.execute(
                "SELECT enabled FROM school_feature_entitlements WHERE school_id=1 AND feature_key='library'"
            ).fetchone()[0]
            b = db.execute(
                "SELECT enabled FROM school_feature_entitlements WHERE school_id=2 AND feature_key='library'"
            ).fetchone()[0]
            self.assertEqual((a,b),(1,0))
        finally:
            db.close()

    def test_csv_formula_prefixes_are_neutralized(self):
        for value in ("=1+1", "+SUM(A1:A2)", "-10", "@cmd", "\tvalue"):
            self.assertTrue(_csv_cell(value).startswith("'"))
        self.assertEqual(_csv_cell("ordinary text"), "ordinary text")

    def test_empty_values_are_safe(self):
        self.assertEqual(_csv_cell(None), "")


if __name__ == "__main__":
    unittest.main()
