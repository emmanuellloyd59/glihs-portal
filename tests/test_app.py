"""Regression tests for the CMHS Digital Campus version 0.1 demo.

Run with: python -m unittest discover -s tests -v
Tests use a temporary SQLite database and fictional accounts only.
"""
import os
import io
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


class PortalTestSupport:
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
        # New Version 3 modules reference users and students. Clear dependent rows
        # first so repeated test runs remain isolated and respect SQLite foreign keys.
        existing_tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table_name in ("finance_payments", "school_announcements", "learning_materials"):
            if table_name in existing_tables:
                connection.execute("DELETE FROM " + table_name)
        connection.execute("DELETE FROM grades")
        connection.execute("DELETE FROM attendance")
        connection.execute("DELETE FROM report_cards")
        connection.execute("DELETE FROM student_finances")
        connection.execute("DELETE FROM audit_log")
        connection.execute("DELETE FROM students")
        connection.execute("DELETE FROM users")
        connection.execute(
            "INSERT INTO users (username, password_hash, role, school_id) VALUES (?, ?, ?, 1)",
            ("teacher_demo", generate_password_hash("test-teacher-password"), "teacher"),
        )
        connection.execute(
            "INSERT INTO users (username, password_hash, role, school_id) VALUES (?, ?, ?, 1)",
            ("student_demo", generate_password_hash("test-student-password"), "student"),
        )
        connection.execute(
            """INSERT INTO users
               (username, password_hash, role, status, is_super_admin, school_id)
               VALUES (?, ?, 'super_admin', 'active', 1, NULL)""",
            ("super_demo", generate_password_hash("test-super-password")),
        )
        connection.execute(
            "INSERT INTO users (username, password_hash, role, school_id) VALUES (?, ?, 'finance_admin', 1)",
            ("finance_demo", generate_password_hash("test-finance-password")),
        )
        connection.execute(
            "INSERT INTO users (username, password_hash, role, school_id) VALUES (?, ?, 'registrar', 1)",
            ("registrar_demo", generate_password_hash("test-registrar-password")),
        )
        connection.execute(
            "INSERT INTO users (username, password_hash, role, school_id) VALUES (?, ?, 'principal', 1)",
            ("principal_demo", generate_password_hash("test-principal-password")),
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





class CMHSPortalTests(PortalTestSupport, unittest.TestCase):
    def test_health_check_reports_ok_without_exposing_database_details(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertTrue(response.is_json)
        self.assertNotIn(b"glihs.db", response.data)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

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

    def test_baseline_security_headers_are_set(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertIn("camera=()", response.headers.get("Permissions-Policy", ""))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_csrf_protection_rejects_post_without_token(self):
        previous = portal.app.config["WTF_CSRF_ENABLED"]
        portal.app.config["WTF_CSRF_ENABLED"] = True
        try:
            response = self.client.post("/login", data={"username": "teacher_demo", "password": "test-teacher-password"})
            self.assertEqual(response.status_code, 400)
        finally:
            portal.app.config["WTF_CSRF_ENABLED"] = previous

    def test_logout_cannot_be_triggered_by_get_request(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get("/logout")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_suspending_an_active_user_invalidates_the_existing_session(self):
        self.login_as("teacher_demo", "test-teacher-password")
        connection = portal.get_db_connection()
        try:
            connection.execute("UPDATE users SET status = 'suspended' WHERE username = 'teacher_demo'")
            connection.commit()
        finally:
            connection.close()
        response = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn(b"Portal Login", response.data)
        self.assertNotIn(b"Dashboard Overview", response.data)

    def test_failed_login_is_audited_without_revealing_account_existence(self):
        response = self.client.post(
            "/login",
            data={"username": "missing_demo", "password": "incorrect"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid username or password", response.data)
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT action, target_username FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(row["action"], "login_failed")
            self.assertEqual(row["target_username"], "missing_demo")
        finally:
            connection.close()

    def test_database_connections_enforce_foreign_keys(self):
        connection = portal.get_db_connection()
        try:
            enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(enabled, 1)
        finally:
            connection.close()

    def test_teacher_dashboard_marks_grades_and_attendance_available(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Class Grades & Attendance", response.data)
        self.assertIn(b"Open Grades & Attendance", response.data)
        self.assertIn(b"badge text-bg-success", response.data)

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

    def test_initial_admin_bootstrap_creates_account_only_when_username_is_unused(self):
        from unittest.mock import patch
        from werkzeug.security import check_password_hash

        # Existing account must not be reset or promoted by startup environment.
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("render_admin", generate_password_hash("old-password"), "teacher"),
        )
        connection.commit()
        connection.close()

        with patch.dict(
            os.environ,
            {
                "INITIAL_ADMIN_USERNAME": "render_admin",
                "INITIAL_ADMIN_PASSWORD": "new-demo-password-long",
            },
        ):
            portal.initialize_database()

        existing = portal.get_user_by_username("render_admin")
        self.assertIsNotNone(existing)
        self.assertEqual(existing.role, "teacher")
        self.assertFalse(existing.is_super_admin)
        self.assertTrue(check_password_hash(existing.password_hash, "old-password"))
        self.assertFalse(check_password_hash(existing.password_hash, "new-demo-password-long"))

        # A new, unique username can still be bootstrapped once.
        with patch.dict(
            os.environ,
            {
                "INITIAL_ADMIN_USERNAME": "first_super_admin",
                "INITIAL_ADMIN_PASSWORD": "new-demo-password-long",
            },
        ):
            portal.initialize_database()

        created = portal.get_user_by_username("first_super_admin")
        self.assertIsNotNone(created)
        self.assertEqual(created.role, "super_admin")
        self.assertTrue(created.is_super_admin)
        self.assertTrue(check_password_hash(created.password_hash, "new-demo-password-long"))

        # Changing the environment password on a later startup must not reset it.
        with patch.dict(
            os.environ,
            {
                "INITIAL_ADMIN_USERNAME": "first_super_admin",
                "INITIAL_ADMIN_PASSWORD": "different-password-long",
            },
        ):
            portal.initialize_database()
        unchanged = portal.get_user_by_username("first_super_admin")
        self.assertTrue(check_password_hash(unchanged.password_hash, "new-demo-password-long"))
        self.assertFalse(check_password_hash(unchanged.password_hash, "different-password-long"))

    def test_initial_admin_bootstrap_ignores_short_passwords(self):
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {
                "INITIAL_ADMIN_USERNAME": "weak_admin",
                "INITIAL_ADMIN_PASSWORD": "short",
            },
        ):
            portal.initialize_database()

        self.assertIsNone(portal.get_user_by_username("weak_admin"))

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
                "school_slug": "legacy-demo-campus",
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
                "school_slug": "legacy-demo-campus",
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

    def test_feature_center_page_loads_and_explains_planned_modules(self):
        response = self.client.get("/features")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Feature Center", response.data)
        self.assertIn(b"Grades and attendance", response.data)
        self.assertIn(b"Planned", response.data)

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



    def test_teacher_can_save_grade_and_attendance(self):
        self.login_as("teacher_demo", "test-teacher-password")
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, 1)",
            ("ACADEMIC-001", "Jamie Example", "Grade 7"),
        )
        student_id = connection.execute(
            "SELECT id FROM students WHERE admission_number = ?", ("ACADEMIC-001",)
        ).fetchone()["id"]
        connection.commit()
        connection.close()

        grade_response = self.client.post("/academics", data={
            "action": "grade", "student_id": str(student_id), "subject": "Mathematics",
            "assessment": "Quiz 1", "score": "18", "max_score": "20",
            "academic_year": "2026-2027", "term": "Term 1",
        }, follow_redirects=True)
        self.assertEqual(grade_response.status_code, 200)
        self.assertIn(b"Grade saved successfully", grade_response.data)
        self.assertIn(b"Mathematics", grade_response.data)

        attendance_response = self.client.post("/academics", data={
            "action": "attendance", "student_id": str(student_id),
            "attendance_date": "2026-10-19", "status": "present", "note": "Demo attendance",
        }, follow_redirects=True)
        self.assertEqual(attendance_response.status_code, 200)
        self.assertIn(b"Attendance saved successfully", attendance_response.data)
        self.assertIn(b"Present", attendance_response.data)

    def test_student_and_finance_roles_cannot_open_academics(self):
        self.login_as("student_demo", "test-student-password")
        self.assertEqual(self.client.get("/academics").status_code, 403)
        self.client.post("/logout")
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, school_id) VALUES (?, ?, ?, 1)",
            ("finance_demo", generate_password_hash("test-finance-password"), "finance_admin"),
        )
        connection.commit()
        connection.close()
        self.login_as("finance_demo", "test-finance-password")
        self.assertEqual(self.client.get("/academics").status_code, 403)

    def test_duplicate_attendance_and_invalid_grade_are_rejected(self):
        self.login_as("teacher_demo", "test-teacher-password")
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, 1)",
            ("ACADEMIC-002", "Alex Example", "Grade 8"),
        )
        student_id = connection.execute(
            "SELECT id FROM students WHERE admission_number = ?", ("ACADEMIC-002",)
        ).fetchone()["id"]
        connection.commit()
        connection.close()
        invalid = self.client.post("/academics", data={
            "action": "grade", "student_id": str(student_id), "subject": "Science",
            "assessment": "Test", "score": "120", "max_score": "100",
            "academic_year": "2026-2027", "term": "Term 1",
        }, follow_redirects=True)
        self.assertIn(b"Scores must be valid numbers", invalid.data)
        data = {
            "action": "attendance", "student_id": str(student_id),
            "attendance_date": "2026-10-19", "status": "present", "note": "",
        }
        self.client.post("/academics", data=data)
        duplicate = self.client.post("/academics", data=data, follow_redirects=True)
        self.assertIn(b"Attendance has already been recorded", duplicate.data)

    def _student_count(self):
        connection = portal.get_db_connection()
        try:
            return connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        finally:
            connection.close()

    def test_registration_rejects_invalid_guardian_phone_without_saving(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.post("/students/register", data={
            "admission_number": "SEC-001", "full_name": "Jordan Example",
            "class_level": "Grade 7", "guardian_phone": "call-me-now",
        }, follow_redirects=True)
        self.assertIn(b"valid guardian phone number", response.data)
        self.assertEqual(self._student_count(), 0)

    def test_registration_rejects_future_birth_date_and_invalid_admission_number(self):
        self.login_as("teacher_demo", "test-teacher-password")
        for admission, dob in (("SEC-002", "2999-01-01"), ("bad admission!", "2012-05-04")):
            response = self.client.post("/students/register", data={
                "admission_number": admission, "full_name": "Jordan Example",
                "class_level": "Grade 7", "date_of_birth": dob,
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self._student_count(), 0)

    def test_student_registration_rejects_invalid_enrollment_date_and_emergency_phone(self):
        self.login_as("teacher_demo", "test-teacher-password")
        base_data = {
            "admission_number": "REG-VALIDATION-001",
            "full_name": "Avery Fiction",
            "class_level": "Grade 8",
            "date_of_birth": "2012-01-10",
            "guardian_name": "Morgan Fiction",
            "guardian_phone": "+231 770 000 000",
            "enrollment_date": "2026-10-20",
            "previous_school": "Fictional Primary School",
            "emergency_contact_name": "Jordan Fiction",
            "emergency_contact_phone": "+231 770 000 001",
            "record_notes": "",
        }
        invalid_cases = (
            {"enrollment_date": "2026-02-31"},
            {"emergency_contact_phone": "not-a-phone"},
        )
        for index, changes in enumerate(invalid_cases):
            data = dict(base_data)
            data["admission_number"] = "REG-VALIDATION-" + str(index + 1)
            data.update(changes)
            response = self.client.post(
                "/students/register", data=data, follow_redirects=True
            )
            self.assertEqual(response.status_code, 200)

        connection = portal.get_db_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM students WHERE admission_number LIKE 'REG-VALIDATION-%'"
            ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            connection.close()

    def test_student_edit_rejects_invalid_phone_and_future_birth_date(self):
        connection = portal.get_db_connection()
        try:
            cursor = connection.execute(
                """INSERT INTO students
                   (admission_number, full_name, class_level, school_id)
                   VALUES (?, ?, ?, 1)""",
                ("EDIT-VALIDATION-001", "Riley Example", "Grade 7"),
            )
            student_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()

        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO users (username, password_hash, role, school_id)
                   VALUES (?, ?, 'principal', 1)""",
                ("principal_edit_demo", generate_password_hash("principal-demo-secret-2026")),
            )
            connection.commit()
        finally:
            connection.close()
        self.login_as("principal_edit_demo", "principal-demo-secret-2026")
        invalid_cases = (
            {"guardian_phone": "abc<script>", "date_of_birth": "2010-01-01"},
            {"guardian_phone": "000-000-0000", "date_of_birth": "2099-01-01"},
        )
        for changes in invalid_cases:
            response = self.client.post(
                f"/students/{student_id}/edit",
                data={
                    "full_name": "Riley Example",
                    "class_level": "Grade 7",
                    "date_of_birth": changes["date_of_birth"],
                    "guardian_name": "Casey Example",
                    "guardian_phone": changes["guardian_phone"],
                    "enrollment_date": "",
                    "previous_school": "",
                    "emergency_contact_name": "",
                    "emergency_contact_phone": "",
                    "record_notes": "",
                },
                follow_redirects=True,
            )
            if changes["guardian_phone"].startswith("abc"):
                self.assertIn(b"valid phone number", response.data)
            else:
                self.assertIn(b"future", response.data)

        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT date_of_birth, guardian_phone FROM students WHERE id = ?",
                (student_id,),
            ).fetchone()
            self.assertIsNone(row["date_of_birth"])
            self.assertIsNone(row["guardian_phone"])
        finally:
            connection.close()

    def test_teacher_student_list_hides_private_fields(self):
        connection = portal.get_db_connection()
        connection.execute(
            """INSERT INTO students
               (admission_number, full_name, class_level, date_of_birth, guardian_name, guardian_phone, school_id)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            ("PRIVACY-001", "Jordan Example", "Grade 7", "2012-05-04",
             "Private Guardian", "+231 770 000 000"),
        )
        connection.commit()
        connection.close()
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get("/students")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jordan Example", response.data)
        self.assertNotIn(b"Private Guardian", response.data)
        self.assertNotIn(b"+231 770 000 000", response.data)
        self.assertNotIn(b"2012-05-04", response.data)
        self.assertNotIn(b"Parent / Guardian", response.data)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_authenticated_pages_are_not_cached(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertIn("private", response.headers.get("Cache-Control", ""))
        self.assertEqual(response.headers.get("Pragma"), "no-cache")




    def test_suspended_school_loses_access_to_existing_tenant_sessions(self):
        self.login_as("teacher_demo", "test-teacher-password")
        connection = portal.get_db_connection()
        try:
            connection.execute("UPDATE schools SET status = 'suspended' WHERE id = 1")
            connection.commit()
        finally:
            connection.close()

        try:
            # Suspended-school sessions are invalidated at user reload, so protected
            # routes redirect to login instead of returning tenant-scoped data.
            self.assertEqual(self.client.get("/students").status_code, 302)
            self.assertEqual(self.client.get("/academics").status_code, 302)
            self.login_as("super_demo", "test-super-password")
            self.assertEqual(self.client.get("/student-finances").status_code, 403)
        finally:
            connection = portal.get_db_connection()
            try:
                connection.execute("UPDATE schools SET status = 'active' WHERE id = 1")
                connection.commit()
            finally:
                connection.close()

    def test_school_one_cannot_view_school_two_student_record(self):
        connection = portal.get_db_connection()
        try:
            connection.execute("INSERT INTO schools (name, slug) VALUES (?, ?)", ("Fictional School Two", "fictional-school-two"))
            school_two_id = connection.execute("SELECT id FROM schools WHERE slug = ?", ("fictional-school-two",)).fetchone()["id"]
            cursor = connection.execute("INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, ?)", ("SCHOOL2-001", "Morgan Fiction", "Grade 6", school_two_id))
            school_two_student_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()

        self.login_as("teacher_demo", "test-teacher-password")
        detail = self.client.get("/students/" + str(school_two_student_id))
        self.assertEqual(detail.status_code, 404)
        listing = self.client.get("/students")
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn(b"Morgan Fiction", listing.data)
        academics = self.client.get("/academics")
        self.assertEqual(academics.status_code, 200)
        self.assertNotIn(b"Morgan Fiction", academics.data)

    def test_school_one_cannot_write_grades_or_attendance_for_school_two_student(self):
        connection = portal.get_db_connection()
        try:
            school = connection.execute(
                "SELECT id FROM schools WHERE slug = ?", ("fictional-school-write-test",)
            ).fetchone()
            if school:
                school_two_id = school["id"]
            else:
                connection.execute(
                    "INSERT INTO schools (name, slug) VALUES (?, ?)",
                    ("Fictional School Write Test", "fictional-school-write-test"),
                )
                school_two_id = connection.execute(
                    "SELECT id FROM schools WHERE slug = ?", ("fictional-school-write-test",)
                ).fetchone()["id"]
            cursor = connection.execute(
                """INSERT INTO students
                   (admission_number, full_name, class_level, school_id)
                   VALUES (?, ?, ?, ?)""",
                ("SCHOOL2-WRITE-001", "Taylor Fiction", "Grade 6", school_two_id),
            )
            school_two_student_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()

        self.login_as("teacher_demo", "test-teacher-password")
        for action_data in (
            {
                "action": "grade", "student_id": str(school_two_student_id),
                "subject": "Mathematics", "assessment": "Unauthorized test",
                "academic_year": "2026-2027", "term": "Term 1",
                "score": "80", "max_score": "100",
            },
            {
                "action": "attendance", "student_id": str(school_two_student_id),
                "attendance_date": "2026-10-20", "status": "present",
                "note": "Unauthorized test",
            },
        ):
            response = self.client.post("/academics", data=action_data)
            self.assertIn(response.status_code, (302, 303))

        connection = portal.get_db_connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM grades WHERE student_id = ?",
                    (school_two_student_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM attendance WHERE student_id = ?",
                    (school_two_student_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_school_one_cannot_read_or_mutate_school_two_report_cards_or_finances(self):
        connection = portal.get_db_connection()
        try:
            school = connection.execute(
                "SELECT id FROM schools WHERE slug = ?", ("fictional-school-finance-isolation",)
            ).fetchone()
            if school:
                school_two_id = school["id"]
            else:
                connection.execute(
                    "INSERT INTO schools (name, slug) VALUES (?, ?)",
                    ("Fictional Finance School", "fictional-school-finance-isolation"),
                )
                school_two_id = connection.execute(
                    "SELECT id FROM schools WHERE slug = ?", ("fictional-school-finance-isolation",)
                ).fetchone()["id"]
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE username = 'teacher_demo'"
            ).fetchone()["id"]
            super_id = connection.execute(
                "SELECT id FROM users WHERE username = 'super_demo'"
            ).fetchone()["id"]
            student_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES (?, ?, ?, ?)""",
                ("SCHOOL2-FINANCE-001", "Finance School Two Student", "Grade 8", school_two_id),
            ).lastrowid
            connection.execute(
                """INSERT INTO grades
                   (student_id, subject, assessment, score, max_score, recorded_by,
                    academic_year, term, school_id)
                   VALUES (?, 'Science', 'School Two Quiz', 18, 20, ?, '2026-2027', 'Term 1', ?)""",
                (student_id, teacher_id, school_two_id),
            )
            connection.execute(
                """INSERT INTO report_cards
                   (student_id, academic_year, term, published, published_by, published_at, school_id)
                   VALUES (?, '2026-2027', 'Term 1', 1, ?, CURRENT_TIMESTAMP, ?)""",
                (student_id, super_id, school_two_id),
            )
            connection.execute(
                """INSERT INTO student_finances
                   (student_id, academic_year, total_fee, amount_paid, notes, updated_by, school_id)
                   VALUES (?, '2026-2027', 1000, 300, 'School Two fictional fees', ?, ?)""",
                (student_id, teacher_id, school_two_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as("principal_demo", "test-principal-password")
        finance_page = self.client.get("/student-finances?academic_year=2026-2027")
        self.assertEqual(finance_page.status_code, 200)
        self.assertNotIn(b"Finance School Two Student", finance_page.data)
        report_page = self.client.get(
            f"/report-cards?student_id={student_id}&academic_year=2026-2027&term=Term%201"
        )
        self.assertEqual(report_page.status_code, 200)
        self.assertNotIn(b"Finance School Two Student", report_page.data)
        self.assertNotIn(b"School Two Quiz", report_page.data)

        self.client.post("/report-cards", data={
            "action": "unpublish", "student_id": str(student_id),
            "academic_year": "2026-2027", "term": "Term 1",
        }, follow_redirects=True)
        self.client.post("/student-finances", data={
            "student_id": str(student_id), "academic_year": "2026-2027",
            "total_fee": "500", "amount_paid": "100", "notes": "attempted cross-school edit",
        }, follow_redirects=True)

        connection = portal.get_db_connection()
        try:
            report = connection.execute(
                "SELECT published FROM report_cards WHERE student_id = ? AND school_id = ?",
                (student_id, school_two_id),
            ).fetchone()
            finance = connection.execute(
                "SELECT total_fee, amount_paid FROM student_finances WHERE student_id = ? AND school_id = ?",
                (student_id, school_two_id),
            ).fetchone()
            self.assertEqual(report["published"], 1)
            self.assertEqual((finance["total_fee"], finance["amount_paid"]), (1000.0, 300.0))
        finally:
            connection.close()

    def test_legacy_student_migration_preserves_records_and_scopes_admission_numbers(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("CREATE TABLE schools (id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE)")
            connection.execute("INSERT INTO schools (id, name, slug) VALUES (1, 'Legacy School', 'legacy-school')")
            connection.execute("INSERT INTO schools (id, name, slug) VALUES (2, 'Second School', 'second-school')")
            connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO users (id) VALUES (7)")
            connection.execute(
                """CREATE TABLE students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admission_number TEXT NOT NULL UNIQUE,
                    full_name TEXT NOT NULL,
                    class_level TEXT NOT NULL,
                    date_of_birth TEXT,
                    guardian_name TEXT,
                    guardian_phone TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    account_user_id INTEGER REFERENCES users(id),
                    enrollment_date TEXT,
                    previous_school TEXT,
                    emergency_contact_name TEXT,
                    emergency_contact_phone TEXT,
                    record_notes TEXT,
                    student_id_number TEXT,
                    school_id INTEGER REFERENCES schools(id)
                )"""
            )
            connection.execute(
                """INSERT INTO students
                   (id, admission_number, full_name, class_level, date_of_birth,
                    guardian_name, guardian_phone, status, account_user_id,
                    enrollment_date, previous_school, emergency_contact_name,
                    emergency_contact_phone, record_notes, student_id_number, school_id)
                   VALUES (42, 'LEGACY-042', 'Fictional Legacy Student', 'Grade 7',
                    '2012-04-05', 'Guardian Example', '000-123-4567', 'active', 7,
                    '2026-09-01', 'Example Primary', 'Contact Example', '000-765-4321',
                    'Preserve this note', 'CMHS-LEGACY-00042', 1)"""
            )
            connection.commit()
            connection.execute("PRAGMA foreign_keys = OFF")
            portal.migrate_student_admission_number_uniqueness(connection)
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")

            row = connection.execute(
                """SELECT id, admission_number, full_name, guardian_name, previous_school,
                          record_notes, student_id_number, school_id
                   FROM students WHERE id = 42"""
            ).fetchone()
            self.assertEqual(
                tuple(row),
                (42, "LEGACY-042", "Fictional Legacy Student", "Guardian Example",
                 "Example Primary", "Preserve this note", "CMHS-LEGACY-00042", 1),
            )
            connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('LEGACY-042', 'Second School Student', 'Grade 7', 2)"""
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO students (admission_number, full_name, class_level, school_id)
                       VALUES ('LEGACY-042', 'Duplicate Legacy Student', 'Grade 7', 1)"""
                )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()

    def test_v03_school_ownership_schema_is_created_and_migration_is_idempotent(self):
        # Re-running startup migration must not duplicate the default school or
        # discard existing fictional records.
        connection = portal.get_db_connection()
        try:
            school = connection.execute(
                "SELECT id, slug FROM schools WHERE id = 1"
            ).fetchone()
            self.assertIsNotNone(school)
            self.assertEqual(school["slug"], "legacy-demo-campus")
            for table in (
                "users", "students", "student_finances", "grades",
                "report_cards", "attendance", "audit_log",
            ):
                columns = {
                    row["name"] for row in connection.execute(
                        "PRAGMA table_info(" + table + ")"
                    ).fetchall()
                }
                self.assertIn("school_id", columns, table)
        finally:
            connection.close()

        portal.initialize_database()
        connection = portal.get_db_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) AS n FROM schools WHERE slug = 'legacy-demo-campus'"
            ).fetchone()["n"]
            self.assertEqual(count, 1)
            teacher = connection.execute(
                "SELECT school_id FROM users WHERE username = 'teacher_demo'"
            ).fetchone()
            self.assertIsNotNone(teacher)
            self.assertEqual(teacher["school_id"], 1)
        finally:
            connection.close()


    def test_unassigned_school_user_cannot_create_student_records(self):
        self.login_as("teacher_demo", "test-teacher-password")
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE users SET school_id = NULL WHERE username = ?",
                ("teacher_demo",),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.post(
            "/students/register",
            data={
                "admission_number": "NO-SCHOOL-001",
                "full_name": "Fictional Unassigned Student",
                "class_level": "Grade 7",
            },
        )
        # An account with no tenant assignment cannot authenticate, so the
        # protected POST redirects to login and must not write any student record.
        self.assertEqual(response.status_code, 302)
        connection = portal.get_db_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM students WHERE admission_number = ?",
                ("NO-SCHOOL-001",),
            ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            connection.close()



    def test_admission_numbers_are_unique_per_school_not_globally(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug) VALUES (?, ?)",
                ("Fictional School Admission Test", "fictional-admission-test"),
            )
            school_two_id = connection.execute(
                "SELECT id FROM schools WHERE slug = ?",
                ("fictional-admission-test",),
            ).fetchone()["id"]
            connection.execute(
                "INSERT INTO students (admission_number, full_name, class_level, school_id) "
                "VALUES (?, ?, ?, 1)",
                ("SHARED-ADM-001", "Student One Example", "Grade 7"),
            )
            connection.execute(
                "INSERT INTO students (admission_number, full_name, class_level, school_id) "
                "VALUES (?, ?, ?, ?)",
                ("SHARED-ADM-001", "Student Two Example", "Grade 7", school_two_id),
            )
            connection.commit()
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM students WHERE admission_number = ?",
                    ("SHARED-ADM-001",),
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()



    def test_staff_access_request_is_assigned_to_selected_active_school(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES (?, ?, 'active')",
                ("Fictional Request School", "fictional-request-school"),
            )
            school_id = connection.execute(
                "SELECT id FROM schools WHERE slug = ?", ("fictional-request-school",)
            ).fetchone()["id"]
            connection.commit()
        finally:
            connection.close()

        response = self.client.post("/admin/request-access", data={
            "full_name": "Fictional Staff Member",
            "username": "fictional_staff_request",
            "requested_role": "teacher",
            "school_slug": "fictional-request-school",
        })
        self.assertEqual(response.status_code, 302)
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT school_id, status, role, is_super_admin FROM users WHERE username = ?",
                ("fictional_staff_request",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["school_id"], school_id)
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["role"], "teacher")
            self.assertEqual(row["is_super_admin"], 0)
        finally:
            connection.close()

    def test_staff_access_request_rejects_unknown_or_suspended_school(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES (?, ?, 'suspended')",
                ("Fictional Suspended School", "fictional-suspended-school"),
            )
            connection.commit()
        finally:
            connection.close()

        for username, slug in (
            ("unknown_school_request", "does-not-exist"),
            ("suspended_school_request", "fictional-suspended-school"),
        ):
            response = self.client.post("/admin/request-access", data={
                "full_name": "Fictional Staff Member",
                "username": username,
                "requested_role": "teacher",
                "school_slug": slug,
            })
            self.assertEqual(response.status_code, 200)
            connection = portal.get_db_connection()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE username = ?", (username,)
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                connection.close()


    def test_version3_stylesheets_have_cache_busting_versions(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"campus-v02.css?v=20261022-1", response.data)
        self.assertIn(b"campus-v03.css?v=20261022-1", response.data)

    def test_learning_material_upload_is_school_scoped_and_student_class_restricted(self):
        self.login_as("principal_demo", "test-principal-password")
        response = self.client.post("/learning-materials", data={
            "title": "Fictional Grade Seven Notes",
            "description": "Test resource",
            "class_level": "Grade 7",
            "resource_file": (io.BytesIO(b"fictional study notes"), "notes.txt"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Fictional Grade Seven Notes", response.data)
        connection = portal.get_db_connection()
        try:
            material_id = connection.execute(
                "SELECT id FROM learning_materials WHERE title = ?",
                ("Fictional Grade Seven Notes",),
            ).fetchone()["id"]
        finally:
            connection.close()
        self.client.post("/logout")
        self.login_as("student_demo", "test-student-password")
        page = self.client.get("/learning-materials")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"Fictional Grade Seven Notes", page.data)
        download = self.client.get(f"/learning-materials/{material_id}/download")
        self.assertEqual(download.status_code, 404)

    def test_student_sees_only_student_or_all_audience_announcements(self):
        self.login_as("principal_demo", "test-principal-password")
        for title, audience in (("Student Notice Example", "students"),
                                ("Staff Notice Example", "staff")):
            response = self.client.post("/announcements", data={
                "title": title, "body": "Fictional school notice", "audience": audience,
            })
            self.assertIn(response.status_code, (302, 303))
        self.client.post("/logout")
        self.login_as("student_demo", "test-student-password")
        response = self.client.get("/announcements")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Student Notice Example", response.data)
        self.assertNotIn(b"Staff Notice Example", response.data)

    def test_finance_payment_creates_receipt_and_updates_balance(self):
        connection = portal.get_db_connection()
        try:
            student_id = connection.execute(
                """INSERT INTO students
                   (admission_number,full_name,class_level,school_id)
                   VALUES ('PAYMENT-TEST-001','Fictional Payment Student','Grade 7',1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO student_finances
                   (student_id,academic_year,total_fee,amount_paid,updated_by,school_id)
                   VALUES (?, '2026-2027', 100, 0, 1, 1)""",
                (student_id,),
            )
            connection.commit()
        finally:
            connection.close()
        self.login_as("finance_demo", "test-finance-password")
        response = self.client.post("/finance/payments", data={
            "student_id": str(student_id), "academic_year": "2026-2027",
            "amount": "25.00", "method": "cash", "reference": "TEST-CASH-001",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RCPT-", response.data)
        connection = portal.get_db_connection()
        try:
            finance = connection.execute(
                "SELECT amount_paid FROM student_finances WHERE student_id=? AND school_id=1",
                (student_id,),
            ).fetchone()
            payment = connection.execute(
                "SELECT amount,receipt_number FROM finance_payments WHERE student_id=? AND school_id=1",
                (student_id,),
            ).fetchone()
            self.assertEqual(finance["amount_paid"], 25.0)
            self.assertEqual(payment["amount"], 25.0)
            self.assertTrue(payment["receipt_number"].startswith("RCPT-"))
        finally:
            connection.close()

    def test_student_cannot_publish_announcement_or_record_finance_payment(self):
        self.login_as("student_demo", "test-student-password")
        response = self.client.post("/announcements", data={
            "title": "Unauthorized", "body": "Must not publish", "audience": "all",
        })
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/finance/payments")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
