"""Direct-route authorization regression tests for CMHS Digital Campus.

Run with: python -m unittest discover -s tests -v
Uses a temporary SQLite database and fictional accounts only.
"""
import os
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["SECRET_KEY"] = "test-only-secret-key-not-for-deployment"
os.environ["DATABASE_PATH"] = str(Path(_TEMP_DIR.name) / "access_control_test.db")
os.environ["SESSION_COOKIE_SECURE"] = "false"

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402


class DirectRouteAuthorizationTests(unittest.TestCase):
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
        for table in ("grades", "attendance", "report_cards", "audit_log", "students", "users"):
            connection.execute(f"DELETE FROM {table}")
        accounts = [
            ("teacher_route_test", "teacher"),
            ("student_route_test", "student"),
            ("finance_route_test", "finance_admin"),
            ("registrar_route_test", "registrar"),
            ("super_route_test", "super_admin"),
        ]
        for username, role in accounts:
            connection.execute(
                """INSERT INTO users (username, password_hash, role, status, is_super_admin)
                   VALUES (?, ?, ?, 'active', ?)""",
                (username, generate_password_hash("test-password-for-route"), role,
                 1 if role == "super_admin" else 0),
            )
        connection.commit()
        connection.close()
        with self.client.session_transaction() as session:
            session.clear()

    def login_as(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "test-password-for-route"},
            follow_redirects=True,
        )

    def test_student_direct_post_to_academics_is_forbidden(self):
        self.login_as("student_route_test")
        response = self.client.post("/academics", data={
            "action": "attendance", "student_id": "1",
            "attendance_date": "2026-10-19", "status": "present",
        })
        self.assertEqual(response.status_code, 403)

    def test_finance_direct_post_to_academics_is_forbidden(self):
        self.login_as("finance_route_test")
        response = self.client.post("/academics", data={
            "action": "grade", "student_id": "1", "subject": "Math",
            "assessment": "Test", "score": "10", "max_score": "10",
            "academic_year": "2026-2027", "term": "Term 1",
        })
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_publish_report_card_by_direct_post(self):
        self.login_as("teacher_route_test")
        response = self.client.post("/report-cards", data={
            "action": "publish", "student_id": "1",
            "academic_year": "2026-2027", "term": "Term 1",
        })
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_manage_accounts_by_direct_post(self):
        self.login_as("teacher_route_test")
        response = self.client.post("/admin/users", data={
            "action": "suspend", "user_id": "1",
        })
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_register_student_by_direct_post(self):
        self.login_as("student_route_test")
        response = self.client.post("/students/register", data={
            "admission_number": "UNAUTHORIZED-001",
            "full_name": "Fictional Student",
            "class_level": "Grade 7",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        connection = portal.get_db_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM students WHERE admission_number = ?",
                ("UNAUTHORIZED-001",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_registrar_can_view_but_cannot_publish_report_cards(self):
        self.login_as("registrar_route_test")
        self.assertEqual(self.client.get("/report-cards").status_code, 200)
        response = self.client.post("/report-cards", data={
            "action": "publish", "student_id": "1",
            "academic_year": "2026-2027", "term": "Term 1",
        })
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
