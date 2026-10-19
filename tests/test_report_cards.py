"""Version 0.2 report-card and access-control regression tests."""
import os
import tempfile
import unittest

_TEST_DIR = tempfile.TemporaryDirectory()
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production")
os.environ["DATABASE_PATH"] = os.path.join(_TEST_DIR.name, "test.sqlite3")

from werkzeug.security import generate_password_hash  # noqa: E402
from app import app, get_db_connection  # noqa: E402


class ReportCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY="test-only-secret-key")
        connection = get_db_connection()
        try:
            for username, role, is_super in (
                ("test_student", "student", 0),
                ("test_teacher", "teacher", 0),
                ("test_principal", "principal", 0),
            ):
                connection.execute(
                    """INSERT OR IGNORE INTO users
                       (username, full_name, password_hash, role, status, is_super_admin)
                       VALUES (?, ?, ?, ?, 'active', ?)""",
                    (username, username, generate_password_hash("test-password-123"), role, is_super),
                )
            connection.execute(
                """INSERT OR IGNORE INTO students (admission_number, full_name, class_level)
                   VALUES ('DEMO-001', 'Fictional Test Student', 'Grade 7')"""
            )
            student_id = connection.execute(
                "SELECT id FROM students WHERE admission_number = 'DEMO-001'"
            ).fetchone()["id"]
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE username = 'test_teacher'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT OR IGNORE INTO grades
                   (student_id, subject, assessment, score, max_score, recorded_by, academic_year, term)
                   VALUES (?, 'Mathematics', 'Test Quiz', 8, 10, ?, '2026-2027', 'Term 1')""",
                (student_id, teacher_id),
            )
            connection.commit()
        finally:
            connection.close()

    def login_as(self, username):
        connection = get_db_connection()
        try:
            user_id = connection.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
        finally:
            connection.close()
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        return client

    def test_report_card_route_requires_login(self):
        response = app.test_client().get("/report-cards")
        self.assertEqual(response.status_code, 302)

    def test_student_cannot_open_staff_report_builder(self):
        response = self.login_as("test_student").get("/report-cards")
        self.assertEqual(response.status_code, 403)

    def test_teacher_can_preview_report_card(self):
        client = self.login_as("test_teacher")
        connection = get_db_connection()
        try:
            student_id = connection.execute("SELECT id FROM students WHERE admission_number='DEMO-001'").fetchone()["id"]
        finally:
            connection.close()
        response = client.get(f"/report-cards?student_id={student_id}&academic_year=2026-2027&term=Term%201")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mathematics", response.data)
        self.assertIn(b"80.0%", response.data)

    def test_teacher_cannot_publish_report_card(self):
        client = self.login_as("test_teacher")
        connection = get_db_connection()
        try:
            student_id = connection.execute("SELECT id FROM students WHERE admission_number='DEMO-001'").fetchone()["id"]
        finally:
            connection.close()
        response = client.post("/report-cards", data={
            "student_id": str(student_id), "academic_year": "2026-2027", "term": "Term 1", "action": "publish"
        })
        self.assertEqual(response.status_code, 403)

    def test_report_card_table_and_grade_term_columns_exist(self):
        connection = get_db_connection()
        try:
            names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("report_cards", names)
            grade_columns = {row["name"] for row in connection.execute("PRAGMA table_info(grades)")}
            self.assertIn("academic_year", grade_columns)
            self.assertIn("term", grade_columns)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
