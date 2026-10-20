"""Version 0.3 school-module regression tests using fictional data only."""
import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-deployment")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP.name) / "modules_test.db"))
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402


class SchoolModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        cls.client = portal.app.test_client()

    def setUp(self):
        conn = portal.get_db_connection()
        for table in ("timetable_entries", "homework_assignments", "school_announcements", "parent_student_links"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM students")
        conn.execute("DELETE FROM users")
        conn.execute("""INSERT INTO users (username, full_name, password_hash, role, status, is_super_admin)
                        VALUES ('module_admin', 'Module Admin', ?, 'super_admin', 'active', 1)""",
                     (generate_password_hash("test-module-admin-password"),))
        conn.execute("""INSERT INTO users (username, full_name, password_hash, role, status)
                        VALUES ('module_teacher', 'Module Teacher', ?, 'teacher', 'active')""",
                     (generate_password_hash("test-module-teacher-password"),))
        conn.commit()
        conn.close()
        self.client.post("/logout")

    def tearDown(self):
        conn = portal.get_db_connection()
        for table in ("timetable_entries", "homework_assignments", "school_announcements", "parent_student_links"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM audit_log")
        conn.execute("DELETE FROM students")
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()
        self.client.post("/logout")

    def login(self, username, password):
        return self.client.post("/login", data={"username": username, "password": password},
                                follow_redirects=True)

    def test_school_tools_requires_login(self):
        response = self.client.get("/school-tools")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_teacher_can_publish_homework(self):
        self.login("module_teacher", "test-module-teacher-password")
        response = self.client.post("/school-tools", data={
            "action": "add_homework", "title": "Fictional Algebra Practice",
            "class_name": "Grade 8", "subject": "Mathematics",
            "instructions": "Complete questions 1 through 5.",
            "due_date": "2099-05-01", "resource_url": "https://example.com/resource"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Homework assignment published", response.data)
        conn = portal.get_db_connection()
        row = conn.execute("SELECT title, class_name FROM homework_assignments").fetchone()
        conn.close()
        self.assertEqual((row["title"], row["class_name"]), ("Fictional Algebra Practice", "Grade 8"))

    def test_teacher_cannot_create_timetable(self):
        self.login("module_teacher", "test-module-teacher-password")
        response = self.client.post("/school-tools", data={
            "action": "add_timetable", "class_name": "Grade 8", "subject": "Math",
            "teacher_name": "Fictional Teacher", "room": "A1", "day_of_week": "Monday",
            "start_time": "09:00", "end_time": "10:00"
        })
        self.assertEqual(response.status_code, 403)
        conn = portal.get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM timetable_entries").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_timetable_conflict_for_same_class_is_rejected(self):
        self.login("module_admin", "test-module-admin-password")
        first = {"action": "add_timetable", "class_name": "Grade 8", "subject": "Math",
                 "teacher_name": "Teacher One", "room": "A1", "day_of_week": "Monday",
                 "start_time": "09:00", "end_time": "10:00"}
        self.client.post("/school-tools", data=first)
        second = dict(first, subject="Science", teacher_name="Teacher Two",
                      room="B2", start_time="09:30", end_time="10:30")
        response = self.client.post("/school-tools", data=second, follow_redirects=True)
        self.assertIn(b"Timetable conflict", response.data)
        conn = portal.get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM timetable_entries").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_announcement_requires_academic_admin(self):
        self.login("module_teacher", "test-module-teacher-password")
        response = self.client.post("/school-tools", data={
            "action": "add_announcement", "announcement_title": "Fictional Notice",
            "announcement_body": "A demo-only message.", "audience": "everyone"
        })
        self.assertEqual(response.status_code, 403)
        conn = portal.get_db_connection()
        count = conn.execute("SELECT COUNT(*) FROM school_announcements").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
