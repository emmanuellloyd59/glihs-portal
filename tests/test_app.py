"""Regression tests for the CMHS Digital Campus version 0.1 demo.

Run with: python -m unittest discover -s tests -v
Tests use a temporary SQLite database and fictional accounts only.
"""
import os
import tempfile
import sqlite3
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["SECRET_KEY"] = "test-only-secret-key-not-for-deployment"
os.environ["DATABASE_PATH"] = str(Path(_TEMP_DIR.name) / "cmhs_test.db")
os.environ["SESSION_COOKIE_SECURE"] = "false"

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402


class CMHSPortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        portal.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SESSION_COOKIE_SECURE=False,
        )
        cls.client = portal.app.test_client()

    def setUp(self):
        connection = portal.get_db_connection()
        connection.execute("DELETE FROM students")
        connection.execute("DELETE FROM users")
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("teacher_demo", generate_password_hash("test-teacher-password"), "teacher"),
        )
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("student_demo", generate_password_hash("test-student-password"), "student"),
        )
        connection.execute(
            """INSERT INTO users
               (username, password_hash, role, status, is_super_admin)
               VALUES (?, ?, 'super_admin', 'active', 1)""",
            ("super_demo", generate_password_hash("test-super-password")),
        )
        connection.commit()
        connection.close()
        self.client.post("/logout")  # Clear a prior authenticated test session if any.

    def login_as(self, username, password):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def test_home_page_loads_with_new_brand(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CMHS Digital Campus", response.data)
        self.assertIn(b"DEMONSTRATION VERSION", response.data)

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_valid_teacher_login_opens_dashboard(self):
        response = self.login_as("teacher_demo", "test-teacher-password")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Dashboard Overview", response.data)
        self.assertIn(b"Student Records", response.data)

    def test_invalid_password_does_not_authenticate(self):
        response = self.client.post(
            "/login",
            data={"username": "teacher_demo", "password": "wrong-password"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password", response.data)
        self.assertNotIn(b"Dashboard Overview", response.data)

    def test_student_cannot_open_staff_student_records(self):
        self.login_as("student_demo", "test-student-password")
        response = self.client.get("/students", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"not authorized to view student records", response.data)
        self.assertNotIn(b"Registered Students", response.data)

    def test_teacher_can_register_and_search_student_record(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.post(
            "/students/register",
            data={
                "admission_number": "DEMO-001",
                "full_name": "Jordan Example",
                "class_level": "Grade 7",
                "date_of_birth": "",
                "guardian_name": "Casey Example",
                "guardian_phone": "000-000-0000",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jordan Example", response.data)
        self.assertIn(b"studentSearch", response.data)

    def test_initial_admin_environment_bootstraps_and_repairs_account(self):
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("render_admin", generate_password_hash("old-password"), "teacher"),
        )
        connection.commit()
        connection.close()

        from unittest.mock import patch
        with patch.dict(
            os.environ,
            {
                "INITIAL_ADMIN_USERNAME": "render_admin",
                "INITIAL_ADMIN_PASSWORD": "new-demo-password",
            },
        ):
            portal.initialize_database()

        user = portal.get_user_by_username("render_admin")
        self.assertIsNotNone(user)
        self.assertEqual(user.role, "super_admin")
        self.assertTrue(user.is_super_admin)
        from werkzeug.security import check_password_hash
        self.assertTrue(check_password_hash(user.password_hash, "new-demo-password"))
        self.assertFalse(check_password_hash(user.password_hash, "old-password"))


    def test_non_super_admin_cannot_open_administrator_management(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get("/admin/users")
        self.assertEqual(response.status_code, 403)

    def test_staff_request_stays_pending_and_cannot_sign_in(self):
        response = self.client.post(
            "/admin/request-access",
            data={
                "full_name": "Morgan Example",
                "username": "registrar_example",
                "requested_role": "registrar",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        connection = portal.get_db_connection()
        row = connection.execute(
            "SELECT status, role FROM users WHERE username = ?",
            ("registrar_example",),
        ).fetchone()
        connection.close()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["role"], "registrar")
        login_response = self.client.post(
            "/login",
            data={"username": "registrar_example", "password": "anything"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid username or password", login_response.data)
        self.assertNotIn(b"Dashboard Overview", login_response.data)

    def test_super_admin_can_approve_request_and_activate_account(self):
        self.client.post(
            "/admin/request-access",
            data={
                "full_name": "Morgan Example",
                "username": "registrar_example",
                "requested_role": "registrar",
            },
        )
        self.login_as("super_demo", "test-super-password")
        response = self.client.post(
            "/admin/users",
            data={
                "action": "approve",
                "user_id": self._user_id("registrar_example"),
                "temporary_password": "Temporary-Example-2026!",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        connection = portal.get_db_connection()
        row = connection.execute(
            "SELECT status, role, approved_by FROM users WHERE username = ?",
            ("registrar_example",),
        ).fetchone()
        connection.close()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["role"], "registrar")
        self.assertIsNotNone(row["approved_by"])
        self.client.post("/logout")
        login_response = self.client.post(
            "/login",
            data={
                "username": "registrar_example",
                "password": "Temporary-Example-2026!",
            },
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Dashboard Overview", login_response.data)

    def _user_id(self, username):
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            return row["id"]
        finally:
            connection.close()

    def test_suspended_account_cannot_sign_in(self):
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO users (username, password_hash, role, status) VALUES (?, ?, ?, 'suspended')",
            ("suspended_demo", generate_password_hash("test-suspended-password"), "teacher"),
        )
        connection.commit()
        connection.close()
        response = self.client.post(
            "/login",
            data={"username": "suspended_demo", "password": "test-suspended-password"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid username or password", response.data)
        self.assertNotIn(b"Dashboard Overview", response.data)


    def test_legacy_users_table_migrates_without_losing_accounts(self):
        legacy = sqlite3.connect(":memory:")
        legacy.row_factory = sqlite3.Row
        legacy.execute(
            """CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('student', 'teacher', 'admin'))
            )"""
        )
        legacy.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("old_admin", "existing-admin-hash", "admin"),
        )
        legacy.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("old_teacher", "existing-teacher-hash", "teacher"),
        )
        legacy.commit()

        portal.migrate_users_table(legacy)
        rows = legacy.execute(
            "SELECT username, password_hash, role, status, is_super_admin, full_name FROM users ORDER BY id"
        ).fetchall()
        legacy.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["username"], "old_admin")
        self.assertEqual(rows[0]["password_hash"], "existing-admin-hash")
        self.assertEqual(rows[0]["role"], "super_admin")
        self.assertEqual(rows[0]["status"], "active")
        self.assertEqual(rows[0]["is_super_admin"], 1)
        self.assertEqual(rows[0]["full_name"], "old_admin")
        self.assertEqual(rows[1]["username"], "old_teacher")
        self.assertEqual(rows[1]["password_hash"], "existing-teacher-hash")
        self.assertEqual(rows[1]["role"], "teacher")

    def test_duplicate_admission_number_is_rejected_gracefully(self):
        self.login_as("teacher_demo", "test-teacher-password")
        data = {
            "admission_number": "DEMO-DUPLICATE",
            "full_name": "Taylor Example",
            "class_level": "Grade 8",
        }
        first = self.client.post("/students/register", data=data, follow_redirects=True)
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/students/register", data=data, follow_redirects=True)
        self.assertEqual(second.status_code, 200)
        self.assertIn(b"That admission number already exists", second.data)


if __name__ == "__main__":
    unittest.main()
