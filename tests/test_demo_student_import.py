"""Safety tests for the fictional student dataset importer."""
import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts" / "import_demo_students.py"
DATA = ROOT / "data" / "demo_students_k2_grade12.csv"


class DemoStudentImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "staging.sqlite"
        with __import__("sqlite3").connect(self.db_path) as db:
            db.executescript("""
                CREATE TABLE schools (id INTEGER PRIMARY KEY, slug TEXT UNIQUE, status TEXT);
                INSERT INTO schools VALUES (1, 'demo-school', 'active');
                INSERT INTO schools VALUES (2, 'other-school', 'active');
                CREATE TABLE students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admission_number TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    class_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    school_id INTEGER NOT NULL REFERENCES schools(id),
                    UNIQUE(school_id, admission_number)
                );
                INSERT INTO students (admission_number, full_name, class_level, status, school_id)
                VALUES ('REAL-001', 'Existing Fictional Record', 'Grade 7', 'active', 1);
            """)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_import(self, *extra, env=None):
        merged_env = os.environ.copy()
        merged_env.pop("RENDER", None)
        merged_env.pop("APP_ENV", None)
        merged_env.pop("FLASK_ENV", None)
        if env:
            merged_env.update(env)
        return subprocess.run(
            [sys.executable, str(IMPORTER), "--db-path", str(self.db_path),
             "--school-slug", "demo-school", *extra],
            text=True, capture_output=True, env=merged_env, check=False,
        )

    def test_dataset_has_exactly_250_unique_fictional_records_and_valid_levels(self):
        with DATA.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 250)
        self.assertEqual(len({r["admission_number"] for r in rows}), 250)
        valid = {"K2", "K3", "K4", *(f"Grade {i}" for i in range(1, 13))}
        self.assertTrue(all(r["class_level"] in valid for r in rows))
        self.assertTrue(all(r["admission_number"].startswith("DEMO-K12-") for r in rows))

    def test_import_adds_250_to_selected_school_and_preserves_existing_records(self):
        result = self.run_import("--confirm-demo-data")
        self.assertEqual(result.returncode, 0, result.stderr)
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM students WHERE school_id=1 AND admission_number LIKE 'DEMO-K12-%'").fetchone()[0], 250)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM students WHERE school_id=2").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM students WHERE admission_number='REAL-001'").fetchone()[0], 1)

    def test_import_requires_explicit_confirmation(self):
        result = self.run_import()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-demo-data", result.stderr)

    def test_rerun_refuses_duplicates_without_modifying_existing_demo_records(self):
        first = self.run_import("--confirm-demo-data")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_import("--confirm-demo-data")
        self.assertNotEqual(second.returncode, 0)
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM students WHERE school_id=1 AND admission_number LIKE 'DEMO-K12-%'").fetchone()[0], 250)

    def test_refuses_production_environment(self):
        result = self.run_import("--confirm-demo-data", env={"APP_ENV": "production"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("production", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
