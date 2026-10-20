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
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
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
        self.assertEqual(response.status_code, 403)
        connection = portal.get_db_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM students WHERE admission_number = ?",
                ("NO-SCHOOL-001",),
            ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
