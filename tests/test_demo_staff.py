"""Regression tests for one-time demo staff provisioning and tenant isolation."""
import os
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-deployment")
os.environ["DATABASE_PATH"] = str(Path(_TEMP_DIR.name) / "demo_staff_test.db")
os.environ["SESSION_COOKIE_SECURE"] = "false"

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402


class DemoStaffProvisioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        portal.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SESSION_COOKIE_SECURE=False,
        )
        cls.client = portal.app.test_client()

    def setUp(self):
        db = portal.get_db_connection()
        for table in (
            "homework_submissions", "homework_assignments", "parent_student_links",
            "finance_payments", "school_announcements", "learning_materials",
            "class_timetable", "grades", "attendance", "report_cards",
            "student_finances", "audit_log", "students", "users",
        ):
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                db.execute("DELETE FROM " + table)
        db.execute("DELETE FROM schools WHERE id <> 1")
        db.execute(
            """INSERT INTO users (username,password_hash,role,status,is_super_admin,school_id)
               VALUES (?,?, 'super_admin','active',1,NULL)""",
            ("super_demo", generate_password_hash("test-super-password")),
        )
        db.execute(
            """INSERT INTO users (username,password_hash,role,status,is_super_admin,school_id)
               VALUES (?,?, 'teacher','active',0,1)""",
            ("teacher_demo", generate_password_hash("test-teacher-password")),
        )
        db.commit()
        db.close()
        self.client.post("/logout")

    def login_as(self, username, password):
        return self.client.post(
            "/login", data={"username": username, "password": password},
            follow_redirects=True,
        )

    def select_school(self, school_id):
        return self.client.post(
            "/admin/select-school", data={"school_id": str(school_id)},
            follow_redirects=True,
        )

    def activate(self):
        return self.client.post(
            "/staff/demo-team", data={"confirm_demo_team": "yes"}
        )

    def test_super_admin_creates_19_accounts_and_passwords_are_hashed(self):
        self.login_as("super_demo", "test-super-password")
        self.select_school(1)
        response = self.activate()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cmhs_teacher_english_demo", response.data)
        self.assertIn(b"Temporary password", response.data)
        db = portal.get_db_connection()
        rows = db.execute(
            "SELECT username,password_hash FROM users WHERE school_id=1 AND username LIKE 'cmhs_%_demo'"
        ).fetchall()
        self.assertEqual(len(rows), 19)
        self.assertTrue(all(row["password_hash"] and len(row["password_hash"]) > 30 for row in rows))
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM audit_log WHERE school_id=1 AND action='demo_staff_account_created'").fetchone()[0],
            19,
        )
        db.close()

    def test_other_school_gets_unique_usernames_and_repeat_is_safe(self):
        db = portal.get_db_connection()
        db.execute(
            "INSERT INTO schools (id,name,slug,status) VALUES (2,'Second Test School','second-test-school','active')"
        )
        db.commit()
        db.close()

        self.login_as("super_demo", "test-super-password")
        self.select_school(1)
        first = self.activate()
        self.assertEqual(first.status_code, 200)

        self.select_school(2)
        second = self.activate()
        self.assertEqual(second.status_code, 200)
        self.assertIn(b"cmhs_teacher_english_demo_s2", second.data)

        db = portal.get_db_connection()
        for school_id in (1, 2):
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM users WHERE school_id=? AND username LIKE 'cmhs_%_demo%'",
                    (school_id,),
                ).fetchone()[0],
                19,
            )
        self.assertEqual(
            db.execute(
                """SELECT COUNT(*) FROM users a JOIN users b ON a.username=b.username
                   WHERE a.school_id=1 AND b.school_id=2"""
            ).fetchone()[0],
            0,
        )
        db.close()

        repeated = self.activate()
        self.assertEqual(repeated.status_code, 200)
        self.assertIn(b"No new accounts were created", repeated.data)
        db = portal.get_db_connection()
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM users WHERE school_id=2 AND username LIKE 'cmhs_%_demo%'").fetchone()[0],
            19,
        )
        db.close()

    def test_teacher_cannot_activate_team(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.activate()
        self.assertEqual(response.status_code, 403)

    def test_super_admin_role_without_platform_admin_flag_is_denied(self):
        db = portal.get_db_connection()
        db.execute(
            """INSERT INTO users (username,password_hash,role,status,is_super_admin,school_id)
               VALUES (?,?, 'super_admin','active',0,1)""",
            ("malformed_admin", generate_password_hash("test-malformed-password")),
        )
        user_id = db.execute(
            "SELECT id FROM users WHERE username='malformed_admin'"
        ).fetchone()["id"]
        db.commit()
        db.close()
        # Simulate a legacy/malformed authenticated session so this specifically
        # exercises the route guard rather than the login-time consistency check.
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        response = self.activate()
        # The loader may reject malformed privileged accounts before the route
        # runs (302 to login); either that or a direct 403 is a safe denial.
        self.assertIn(response.status_code, (302, 403))
        db = portal.get_db_connection()
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM users WHERE username LIKE 'cmhs_%_demo%'").fetchone()[0],
            0,
        )
        db.close()


if __name__ == "__main__":
    unittest.main()
