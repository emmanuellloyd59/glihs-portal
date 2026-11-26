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
                INSERT INTO users (id, username, password_hash, role)
                    VALUES (1, 'legacy_teacher', 'not-a-real-login-hash', 'teacher');
                INSERT INTO students (id, admission_number, full_name, class_level)
                    VALUES (7, 'LEGACY-007', 'Fictional Legacy Student', 'Grade 7');
                INSERT INTO grades
                    (id, student_id, subject, assessment, score, max_score, recorded_by)
                    VALUES (11, 7, 'Mathematics', 'Quiz 1', 18, 20, 1);
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


    def test_migration_is_atomic_when_legacy_user_copy_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "interrupted_migration.db"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO users (id,username,password_hash,role) VALUES (9,'legacy_bad','hash','invalid_role')"
            )
            connection.commit()
            connection.close()

            original_database = portal.DATABASE
            portal.DATABASE = database_path
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    portal.initialize_database()
                check = sqlite3.connect(database_path)
                try:
                    user = check.execute(
                        "SELECT id,username,password_hash,role FROM users WHERE id=9"
                    ).fetchone()
                    migrating_table = check.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='users_migrating'"
                    ).fetchone()[0]
                    ledger_table = check.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                    ).fetchone()[0]
                    self.assertEqual(user, (9, "legacy_bad", "hash", "invalid_role"))
                    self.assertEqual(migrating_table, 0)
                    self.assertEqual(ledger_table, 0)
                finally:
                    check.close()
            finally:
                portal.DATABASE = original_database

    def test_migration_is_idempotent_and_school_relationships_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "tenant_integrity.db"
            original_database = portal.DATABASE
            portal.DATABASE = database_path
            try:
                portal.initialize_database()
                connection = portal.get_db_connection()
                try:
                    connection.execute(
                        "INSERT INTO schools (id,name,slug) VALUES (2,'Second School','second-school')"
                    )
                    parent_one = connection.execute(
                        """INSERT INTO users (username,password_hash,role,status,is_super_admin,school_id)
                           VALUES ('parent_one','hash','parent','active',0,1)"""
                    ).lastrowid
                    parent_two = connection.execute(
                        """INSERT INTO users (username,password_hash,role,status,is_super_admin,school_id)
                           VALUES ('parent_two','hash','parent','active',0,2)"""
                    ).lastrowid
                    student = connection.execute(
                        """INSERT INTO students
                           (admission_number,full_name,class_level,school_id)
                           VALUES ('TENANT-001','Fictional Tenant Student','Grade 7',1)"""
                    ).lastrowid
                    updater = connection.execute(
                        "SELECT id FROM users WHERE school_id=1 LIMIT 1"
                    ).fetchone()["id"]
                    student_account = connection.execute(
                        """INSERT INTO users (username,password_hash,role,status,is_super_admin,school_id)
                           VALUES ('student_account','hash','student','active',0,1)"""
                    ).lastrowid
                    connection.execute(
                        "UPDATE students SET account_user_id=? WHERE id=?",
                        (student_account, student),
                    )
                    connection.execute(
                        """INSERT INTO parent_student_links (parent_user_id,student_id,school_id,created_by)
                           VALUES (?,?,1,?)""",
                        (parent_one, student, updater),
                    )
                    connection.commit()

                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """INSERT INTO parent_student_links
                               (parent_user_id,student_id,school_id,created_by)
                               VALUES (?,?,1,?)""",
                            (parent_two, student, updater),
                        )
                    connection.rollback()

                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """INSERT INTO student_finances
                               (student_id,academic_year,total_fee,amount_paid,updated_by,school_id)
                               VALUES (?,'2026-2027',8000,0,?,2)""",
                            (student, updater),
                        )
                    connection.rollback()

                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "UPDATE students SET account_user_id=? WHERE id=?",
                            (parent_two, student),
                        )
                    connection.rollback()
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "UPDATE users SET school_id=2 WHERE id=?",
                            (parent_one,),
                        )
                    connection.rollback()
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "UPDATE users SET school_id=2 WHERE id=?",
                            (student_account,),
                        )
                    connection.rollback()
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "UPDATE students SET school_id=2 WHERE id=?",
                            (student,),
                        )
                    connection.rollback()
                finally:
                    connection.close()

                # A second startup migration must preserve the parent-child link.
                portal.initialize_database()
                verify = portal.get_db_connection()
                try:
                    self.assertEqual(
                        verify.execute(
                            "SELECT COUNT(*) FROM parent_student_links WHERE school_id=1"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        verify.execute(
                            "SELECT account_user_id FROM students WHERE admission_number='TENANT-001'"
                        ).fetchone()[0],
                        student_account,
                    )
                    self.assertEqual(
                        verify.execute("PRAGMA foreign_key_check").fetchall(),
                        [],
                    )
                    self.assertEqual(
                        verify.execute(
                            "SELECT version FROM schema_migrations ORDER BY version"
                        ).fetchall(),
                        [(1,), (2,)],
                    )
                    # Finance payment records must agree with both student and
                    # receiver school ownership, not merely carry a caller-supplied ID.
                    portal.ensure_content_tables(verify)
                    with self.assertRaises(sqlite3.IntegrityError):
                        verify.execute(
                            """INSERT INTO finance_payments
                               (school_id,student_id,academic_year,amount,method,receipt_number,received_by)
                               VALUES (1,?,'2026-2027',100,'cash','CROSS-SCHOOL-RECEIPT',?)""",
                            (student, parent_two),
                        )
                    verify.rollback()
                    self.assertEqual(
                        verify.execute(
                            "SELECT version FROM schema_migrations ORDER BY version"
                        ).fetchall(),
                        [(1,), (2,), (3,)],
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        verify.execute(
                            """INSERT INTO learning_materials
                               (school_id,title,description,class_level,stored_name,original_name,uploaded_by)
                               VALUES (1,'Test resource','','Grade 7','test.pdf','test.pdf',?)""",
                            (parent_two,),
                        )
                    verify.rollback()
                    with self.assertRaises(sqlite3.IntegrityError):
                        verify.execute(
                            """INSERT INTO school_announcements
                               (school_id,title,body,audience,created_by,updated_by)
                               VALUES (1,'Cross-school notice','Not allowed','all',?,?)""",
                            (parent_two, parent_two),
                        )
                    verify.rollback()
                    self.assertEqual(
                        verify.execute(
                            "SELECT COUNT(*) FROM students WHERE admission_number='TENANT-001'"
                        ).fetchone()[0],
                        1,
                    )
                finally:
                    verify.close()
            finally:
                portal.DATABASE = original_database


if __name__ == "__main__":
    unittest.main()
