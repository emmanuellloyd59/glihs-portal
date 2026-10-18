"""Regression tests for the CMHS Digital Campus version 0.1 demo.

Run with: python -m unittest discover -s tests -v
Tests use a temporary SQLite database and fictional accounts only.
"""
import os
import tempfile
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
