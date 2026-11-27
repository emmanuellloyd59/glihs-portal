"""Database upgrade regression tests using a disposable legacy SQLite database."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-deployment")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import app as portal  # noqa: E402


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_records_survive_upgrade_and_student_link_column_is_added(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy_school.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('student', 'teacher', 'admin'))
                );
                CREATE TABLE students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admission_number TEXT NOT NULL UNIQUE,
                    full_name TEXT NOT NULL,
                    class_level TEXT NOT NULL,
                    date_of_birth TEXT,
                    guardian_name TEXT,
                    guardian_phone TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE grades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    subject TEXT NOT NULL,
                    assessment TEXT NOT NULL,
                    score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                    max_score REAL NOT NULL CHECK (max_score > 0 AND score <= max_score),
                    recorded_by INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, subject, assessment)
                );
                CREATE TABLE report_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    academic_year TEXT NOT NULL,
                    term TEXT NOT NULL CHECK (term IN ('Term 1', 'Term 2', 'Term 3')),
                    published INTEGER NOT NULL DEFAULT 0 CHECK (published IN (0, 1)),
                    published_by INTEGER REFERENCES users(id),
                    published_at TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, academic_year, term)
                );
                INSERT INTO users (id, username, password_hash, role)
                    VALUES (1, 'legacy_teacher', 'not-a-real-login-hash', 'teacher');
                INSERT INTO students (id, admission_number, full_name, class_level)
                    VALUES (7, 'LEGACY-007', 'Fictional Legacy Student', 'Grade 7');
                INSERT INTO grades
                    (id, student_id, subject, assessment, score, max_score, recorded_by)
                    VALUES (11, 7, 'Mathematics', 'Quiz 1', 18, 20, 1);
                INSERT INTO report_cards
                    (id, student_id, academic_year, term, published, published_by, published_at)
                    VALUES (21, 7, '2024-2025', 'Term 1', 1, 1, '2025-01-10 10:00:00');
                """
            )
            connection.commit()
            connection.close()

            original_database = portal.DATABASE
            portal.DATABASE = database_path
            try:
                portal.initialize_database()
                upgraded = portal.get_db_connection()
                try:
                    user = upgraded.execute(
                        "SELECT id, username, role, status FROM users WHERE id = 1"
                    ).fetchone()
                    student = upgraded.execute(
                        "SELECT id, admission_number, full_name, account_user_id, student_id_number FROM students WHERE id = 7"
                    ).fetchone()
                    grade = upgraded.execute(
                        "SELECT id, student_id, subject, assessment, score, max_score, academic_year, term "
                        "FROM grades WHERE id = 11"
                    ).fetchone()
                    report = upgraded.execute(
                        "SELECT id, student_id, academic_year, term, published, school_id "
                        "FROM report_cards WHERE id = 21"
                    ).fetchone()
                    report_schema = upgraded.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='report_cards'"
                    ).fetchone()["sql"]
                    columns = {
                        row["name"] for row in upgraded.execute("PRAGMA table_info(students)").fetchall()
                    }
                    foreign_key_issues = upgraded.execute("PRAGMA foreign_key_check").fetchall()

                    self.assertEqual((user["id"], user["username"], user["role"], user["status"]),
                                     (1, "legacy_teacher", "teacher", "active"))
                    self.assertEqual((student["id"], student["admission_number"], student["full_name"]),
                                     (7, "LEGACY-007", "Fictional Legacy Student"))
                    self.assertIsNone(student["account_user_id"])
                    self.assertIn("account_user_id", columns)
                    self.assertIn("student_id_number", columns)
                    self.assertIn("enrollment_date", columns)
                    self.assertIn("previous_school", columns)
                    self.assertIn("emergency_contact_name", columns)
                    self.assertIn("emergency_contact_phone", columns)
                    self.assertIn("record_notes", columns)
                    self.assertTrue(student["student_id_number"].startswith("CMHS-"))
                    self.assertEqual(
                        (grade["id"], grade["student_id"], grade["subject"], grade["assessment"],
                         grade["score"], grade["max_score"], grade["academic_year"], grade["term"]),
                        (11, 7, "Mathematics", "Quiz 1", 18.0, 20.0, "Unassigned", "Unassigned"),
                    )
                    self.assertEqual(
                        (report["id"], report["student_id"], report["academic_year"], report["term"], report["published"], report["school_id"]),
                        (21, 7, "2024-2025", "Term 1", 1, 1),
                    )
                    self.assertNotIn("Term 1", report_schema)
                    upgraded.execute(
                        "INSERT INTO report_cards(student_id,academic_year,term,school_id) VALUES(7,'2025/26','Semester A',1)"
                    )
                    self.assertEqual(foreign_key_issues, [])
                    self.assertEqual(
                        upgraded.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
                            "AND name='idx_students_account_user_id'"
                        ).fetchone()[0],
                        1,
                    )
                finally:
                    upgraded.close()
            finally:
                portal.DATABASE = original_database


if __name__ == "__main__":
    unittest.main()
