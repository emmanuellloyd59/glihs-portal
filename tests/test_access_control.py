"""Direct-route authorization regression tests for CMHS Digital Campus.

Run with: python -m unittest discover -s tests -v
Uses a temporary SQLite database and fictional accounts only.
"""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
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
        connection.execute("DELETE FROM parent_student_links")
        for table in ("grades", "attendance", "report_cards", "student_finances", "audit_log", "students", "users"):
            connection.execute(f"DELETE FROM {table}")
        accounts = [
            ("teacher_route_test", "teacher"),
            ("student_route_test", "student"),
            ("finance_route_test", "finance_admin"),
            ("registrar_route_test", "registrar"),
            ("principal_route_test", "principal"),
            ("super_route_test", "super_admin"),
        ]
        for username, role in accounts:
            connection.execute(
                """INSERT INTO users (username, password_hash, role, status, is_super_admin, school_id)
                   VALUES (?, ?, ?, 'active', ?, ?)""",
                (username, generate_password_hash("test-password-for-route"), role,
                 1 if role == "super_admin" else 0,
                 None if role == "super_admin" else 1),
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

    def test_parent_account_creation_requires_identity_verification(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, status, school_id)
                   VALUES ('PRIV-VERIFY-001', 'Verification Test Child', 'Grade 4', 'active', 1)"""
            )
            connection.commit()
        finally:
            connection.close()
        self.login_as("super_route_test")
        response = self.client.post("/admin/users", data={
            "action": "create_account",
            "new_username": "parent_unverified_test",
            "new_full_name": "Fictional Parent",
            "new_role": "parent",
            "new_password": "temporary-test-password-123",
            "school_id": "1",
            "linked_student_id": "1",
        }, follow_redirects=True)
        connection = portal.get_db_connection()
        try:
            account = connection.execute(
                "SELECT id FROM users WHERE username = ?", ("parent_unverified_test",)
            ).fetchone()
            self.assertIsNone(account, "An unverified parent account must not be created.")
        finally:
            connection.close()
        self.assertIn(b"relationship", response.data.lower())

    def test_parent_can_view_only_explicitly_linked_child(self):
        connection = portal.get_db_connection()
        try:
            parent_id = connection.execute(
                """INSERT INTO users (username, password_hash, role, status, school_id)
                   VALUES (?, ?, 'parent', 'active', 1)""",
                ("parent_privacy_test", generate_password_hash("test-password-for-route")),
            ).lastrowid
            linked_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, status, school_id)
                   VALUES ('PRIV-LINKED-001', 'Linked Fictional Child', 'Grade 4', 'active', 1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, status, school_id)
                   VALUES ('PRIV-OTHER-001', 'Unlinked Fictional Child', 'Grade 4', 'active', 1)"""
            )
            connection.execute(
                """INSERT INTO parent_student_links (parent_user_id, student_id, school_id, created_by)
                   VALUES (?, ?, 1, ?)""", (parent_id, linked_id, 1)
            )
            connection.commit()
        finally:
            connection.close()
        self.login_as("parent_privacy_test")
        response = self.client.get("/parent/children")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Linked Fictional Child", response.data)
        self.assertNotIn(b"Unlinked Fictional Child", response.data)

    def test_inconsistent_super_admin_flag_cannot_grant_platform_access(self):
        connection = portal.get_db_connection()
        try:
            # A non-super-admin role must not gain global access from a forged flag.
            connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, status, is_super_admin, school_id)
                   VALUES (?, ?, 'teacher', 'active', 1, NULL)""",
                ("teacher_with_platform_flag", generate_password_hash("test-password-for-route")),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertIsNone(portal.get_user_by_username("teacher_with_platform_flag"))
        response = self.login_as("teacher_with_platform_flag")
        self.assertIn(b"Invalid username or password", response.data)

    def test_super_admin_role_without_platform_flag_cannot_authenticate(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, status, is_super_admin, school_id)
                   VALUES (?, ?, 'super_admin', 'active', 0, 1)""",
                ("role_only_super_admin", generate_password_hash("test-password-for-route")),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertIsNone(portal.get_user_by_username("role_only_super_admin"))
        response = self.login_as("role_only_super_admin")
        self.assertIn(b"Invalid username or password", response.data)

    def test_platform_super_admin_must_select_school_before_school_features(self):
        self.login_as("super_route_test")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Administrator Management", response.data)
        self.assertIn(b"Select School Workspace", response.data)
        self.assertNotIn(b"View Student Records", response.data)
        self.assertNotIn(b"Register Student", response.data)
        self.assertEqual(self.client.get("/students/register").status_code, 403)

    def test_platform_super_admin_can_access_school_features_after_selecting_school(self):
        self.login_as("super_route_test")
        response = self.client.post("/admin/select-school", data={"school_id": "1"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"View Student Records", response.data)
        self.assertIn(b"Register Student", response.data)
        self.assertIn(b"Open Grades & Attendance", response.data)
        self.assertIn(b"Open Report Cards", response.data)
        for path in ("/students", "/students/register", "/academics", "/report-cards",
                     "/finance/payments", "/learning-materials", "/announcements"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_existing_student_link_is_not_reassigned_across_schools(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES ('Link Test School B', 'link-test-school-b', 'active') "
                "ON CONFLICT(slug) DO NOTHING"
            )
            school_b_id = connection.execute(
                "SELECT id FROM schools WHERE slug = 'link-test-school-b'"
            ).fetchone()["id"]
            target_user_id = connection.execute(
                "INSERT INTO users (username, password_hash, role, status, school_id) "
                "VALUES ('link_test_student_account', ?, 'student', 'active', 1)",
                (generate_password_hash("test-password-for-route"),),
            ).lastrowid
            connection.execute(
                "INSERT INTO students (admission_number, full_name, class_level, school_id, account_user_id) "
                "VALUES ('LINK-B-001', 'Fictional School B Student', 'Grade 8', ?, ?)",
                (school_b_id, target_user_id),
            )
            school_a_student_id = connection.execute(
                "INSERT INTO students (admission_number, full_name, class_level, school_id) "
                "VALUES ('LINK-A-001', 'Fictional School A Student', 'Grade 7', 1)"
            ).lastrowid
            connection.commit()
        finally:
            connection.close()

        self.login_as("super_route_test")
        response = self.client.post("/admin/users", data={
            "action": "link_student",
            "user_id": str(target_user_id),
            "student_id": str(school_a_student_id),
            "verify_identity": "yes",
        }, follow_redirects=True)
        self.assertIn(b"already linked to a different student record", response.data)

        connection = portal.get_db_connection()
        try:
            linked = connection.execute(
                "SELECT id, school_id FROM students WHERE account_user_id = ?",
                (target_user_id,),
            ).fetchone()
            self.assertIsNotNone(linked)
            self.assertEqual(linked["school_id"], school_b_id)
            self.assertNotEqual(linked["id"], school_a_student_id)
        finally:
            connection.close()

    def test_school_assigned_super_admin_cannot_authenticate(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, status, is_super_admin, school_id)
                   VALUES (?, ?, 'super_admin', 'active', 1, 1)""",
                ("invalid_school_super_admin", generate_password_hash("test-password-for-route")),
            )
            connection.commit()
        finally:
            connection.close()

        # Platform super-administrators must be platform-scoped (school_id NULL).
        # A school-bound account must not gain global administrator privileges.
        self.assertIsNone(portal.get_user_by_username("invalid_school_super_admin"))
        response = self.login_as("invalid_school_super_admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid username or password", response.data)


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

    def test_staff_approval_audit_event_is_attributed_to_target_school(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO users
                   (username, full_name, password_hash, role, status, is_super_admin, school_id)
                   VALUES (?, ?, ?, 'teacher', 'pending', 0, 1)""",
                ("pending_school_a_teacher", "Fictional Pending Teacher",
                 generate_password_hash("placeholder-password")),
            )
            pending_id = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                ("pending_school_a_teacher",),
            ).fetchone()["id"]
            connection.commit()
        finally:
            connection.close()

        self.login_as("super_route_test")
        response = self.client.post("/admin/users", data={
            "action": "approve",
            "user_id": str(pending_id),
            "temporary_password": "Temporary-School-A-2026!",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        connection = portal.get_db_connection()
        try:
            event = connection.execute(
                """SELECT school_id FROM audit_log
                   WHERE action = 'staff_request_approved'
                     AND target_username = ?
                   ORDER BY id DESC LIMIT 1""",
                ("pending_school_a_teacher",),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(event)
        self.assertEqual(event["school_id"], 1)

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


    def _id_for(self, table, column, value):
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                f"SELECT id FROM {table} WHERE {column} = ?", (value,)
            ).fetchone()
            return row["id"]
        finally:
            connection.close()

    def test_unlinked_student_does_not_see_student_records(self):
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, 1)",
            ("PRIVATE-001", "Private Example", "Grade 8"),
        )
        connection.commit()
        connection.close()
        self.login_as("student_route_test")
        response = self.client.get("/my-report-cards")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Student record not linked yet", response.data)
        self.assertNotIn(b"Private Example", response.data)

    def test_student_record_link_requires_identity_verification_confirmation(self):
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, 1)",
            ("VERIFY-001", "Verification Example", "Grade 9"),
        )
        student_id = connection.execute(
            "SELECT id FROM students WHERE admission_number = 'VERIFY-001'"
        ).fetchone()["id"]
        account_id = connection.execute(
            "SELECT id FROM users WHERE username = 'student_route_test'"
        ).fetchone()["id"]
        connection.commit()
        connection.close()
        self.login_as("super_route_test")
        response = self.client.post("/admin/users", data={
            "action": "link_student", "user_id": str(account_id), "student_id": str(student_id),
        }, follow_redirects=True)
        self.assertIn(b"identity and admission number have been checked before linking", response.data)
        connection = portal.get_db_connection()
        try:
            linked = connection.execute(
                "SELECT account_user_id FROM students WHERE id = ?", (student_id,)
            ).fetchone()["account_user_id"]
        finally:
            connection.close()
        self.assertIsNone(linked)

    def test_super_admin_links_student_and_student_sees_only_own_published_report(self):
        connection = portal.get_db_connection()
        connection.execute(
            "INSERT INTO users (username, password_hash, role, status, school_id) VALUES (?, ?, 'student', 'active', 1)",
            ("student_two_route_test", generate_password_hash("test-password-for-route")),
        )
        connection.execute(
            "INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, 1)",
            ("OWN-001", "Own Student Example", "Grade 7"),
        )
        own_id = connection.execute(
            "SELECT id FROM students WHERE admission_number = 'OWN-001'"
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO students (admission_number, full_name, class_level, school_id) VALUES (?, ?, ?, 1)",
            ("OTHER-001", "Other Student Example", "Grade 8"),
        )
        other_id = connection.execute(
            "SELECT id FROM students WHERE admission_number = 'OTHER-001'"
        ).fetchone()["id"]
        teacher_id = connection.execute(
            "SELECT id FROM users WHERE username = 'teacher_route_test'"
        ).fetchone()["id"]
        super_id = connection.execute(
            "SELECT id FROM users WHERE username = 'super_route_test'"
        ).fetchone()["id"]
        connection.execute(
            """INSERT INTO grades (student_id, subject, assessment, score, max_score,
                   recorded_by, academic_year, term, school_id)
               VALUES (?, 'Own Subject', 'Quiz 1', 18, 20, ?, '2026-2027', 'Term 1', 1)""",
            (own_id, teacher_id),
        )
        connection.execute(
            """INSERT INTO grades (student_id, subject, assessment, score, max_score,
                   recorded_by, academic_year, term, school_id)
               VALUES (?, 'Private Subject', 'Quiz 1', 10, 10, ?, '2026-2027', 'Term 1', 1)""",
            (other_id, teacher_id),
        )
        for sid in (own_id, other_id):
            connection.execute(
                """INSERT INTO report_cards (student_id, academic_year, term, published,
                       published_by, published_at, school_id)
                   VALUES (?, '2026-2027', 'Term 1', 1, ?, CURRENT_TIMESTAMP, 1)""",
                (sid, super_id),
            )
        connection.commit()
        connection.close()

        student_user_id = self._id_for("users", "username", "student_route_test")
        self.login_as("super_route_test")
        link_response = self.client.post("/admin/users", data={
            "action": "link_student", "user_id": str(student_user_id), "student_id": str(own_id),
            "verify_identity": "yes",
        }, follow_redirects=True)
        self.assertEqual(link_response.status_code, 200)
        self.assertIn(b"Own Student Example", link_response.data)

        self.client.post("/logout")
        self.login_as("student_route_test")
        response = self.client.get("/my-report-cards")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Own Student Example", response.data)
        self.assertIn(b"Own Subject", response.data)
        self.assertNotIn(b"Other Student Example", response.data)
        self.assertNotIn(b"Private Subject", response.data)


    def test_teacher_student_list_context_does_not_receive_financial_values(self):
        connection = portal.get_db_connection()
        try:
            student_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('FINANCE-PRIVACY-001', 'Fictional Finance Privacy Student', 'Grade 7', 1)"""
            ).lastrowid
            teacher_id = connection.execute(
                "SELECT id FROM users WHERE username = 'teacher_route_test'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO student_finances
                   (student_id, academic_year, total_fee, amount_paid, updated_by, school_id)
                   VALUES (?, '2026-2027', 1200, 300, ?, 1)""",
                (student_id, teacher_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as("teacher_route_test")
        captured = {}

        def capture_template(template_name, **context):
            captured.update(context)
            return portal.app.response_class("captured template context")

        with patch.object(portal, "render_template", side_effect=capture_template):
            response = self.client.get("/students")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(captured["can_view_finances"])
        self.assertEqual(len(captured["students"]), 1)
        self.assertIsNone(captured["students"][0]["total_fee"])
        self.assertIsNone(captured["students"][0]["amount_paid"])
        self.assertIsNone(captured["students"][0]["balance"])

    def test_principal_student_list_receives_finance_values(self):
        connection = portal.get_db_connection()
        try:
            student_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('PRINCIPAL-FINANCE-001', 'Fictional Principal Finance Student', 'Grade 7', 1)"""
            ).lastrowid
            finance_user_id = connection.execute(
                "SELECT id FROM users WHERE username = 'principal_route_test'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO student_finances
                   (student_id, academic_year, total_fee, amount_paid, updated_by, school_id)
                   VALUES (?, '2026-2027', 1200, 300, ?, 1)""",
                (student_id, finance_user_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as("principal_route_test")
        captured = {}

        def capture_template(template_name, **context):
            captured.update(context)
            return portal.app.response_class("captured template context")

        with patch.object(portal, "render_template", side_effect=capture_template):
            response = self.client.get("/students")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["can_view_finances"])
        self.assertEqual(len(captured["students"]), 1)
        self.assertEqual(captured["students"][0]["total_fee"], 1200)
        self.assertEqual(captured["students"][0]["amount_paid"], 300)
        self.assertEqual(captured["students"][0]["balance"], 900)

    def test_school_a_cannot_read_or_write_school_b_records(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES ('School B Test', 'school-b-test', 'active') "
                "ON CONFLICT(slug) DO NOTHING"
            )
            school_b_id = connection.execute(
                "SELECT id FROM schools WHERE slug = 'school-b-test'"
            ).fetchone()["id"]
            teacher_b_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, status, school_id)
                   VALUES ('teacher_school_b', ?, 'teacher', 'active', ?)""",
                (generate_password_hash("test-password-for-route"), school_b_id),
            ).lastrowid
            student_b_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('SCHOOL-B-001', 'School B Fictional Student', 'Grade 8', ?)""",
                (school_b_id,),
            ).lastrowid
            connection.execute(
                """INSERT INTO grades
                   (student_id, subject, assessment, score, max_score, recorded_by,
                    academic_year, term, school_id)
                   VALUES (?, 'Private Mathematics', 'Quiz 1', 8, 10, ?,
                           '2026-2027', 'Term 1', ?)""",
                (student_b_id, teacher_b_id, school_b_id),
            )
            # Seed intentionally inconsistent ownership to verify that joins fail
            # closed even if a legacy/manual write leaves a cross-school actor link.
            student_a_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('SCHOOL-A-MISMATCH-001', 'School A Join Test Student', 'Grade 7', 1)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO grades
                   (student_id, subject, assessment, score, max_score, recorded_by,
                    academic_year, term, school_id)
                   VALUES (?, 'Mismatched Staff Subject', 'Quiz 1', 8, 10, ?,
                           '2026-2027', 'Term 1', 1)""",
                (student_a_id, teacher_b_id),
            )
            connection.execute(
                """INSERT INTO attendance
                   (student_id, attendance_date, status, note, recorded_by, school_id)
                   VALUES (?, '2026-10-22', 'present', 'Mismatched Staff Attendance', ?, 1)""",
                (student_a_id, teacher_b_id),
            )
            connection.execute(
                """INSERT INTO attendance
                   (student_id, attendance_date, status, recorded_by, school_id)
                   VALUES (?, '2026-10-20', 'present', ?, ?)""",
                (student_b_id, teacher_b_id, school_b_id),
            )
            connection.execute(
                """INSERT INTO report_cards
                   (student_id, academic_year, term, published, school_id)
                   VALUES (?, '2026-2027', 'Term 1', 1, ?)""",
                (student_b_id, school_b_id),
            )
            connection.execute(
                """INSERT INTO student_finances
                   (student_id, academic_year, total_fee, amount_paid, updated_by, school_id)
                   VALUES (?, '2026-2027', 1000, 250, ?, ?)""",
                (student_b_id, teacher_b_id, school_b_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as("teacher_route_test")
        self.assertEqual(self.client.get(f"/students/{student_b_id}").status_code, 404)
        student_search = self.client.get("/students?q=School+B")
        self.assertEqual(student_search.status_code, 200)
        self.assertNotIn(b"School B Fictional Student", student_search.data)

        academic_page = self.client.get("/academics")
        self.assertEqual(academic_page.status_code, 200)
        self.assertNotIn(b"School B Fictional Student", academic_page.data)
        self.assertNotIn(b"Private Mathematics", academic_page.data)
        self.assertNotIn(b"Mismatched Staff Subject", academic_page.data)
        self.assertNotIn(b"Mismatched Staff Attendance", academic_page.data)

        # A foreign student ID in a grade or attendance POST must not create records.
        for payload in (
            {
                "action": "grade", "student_id": str(student_b_id),
                "subject": "Unauthorized Subject", "assessment": "Quiz",
                "score": "8", "max_score": "10",
                "academic_year": "2026-2027", "term": "Term 1",
            },
            {
                "action": "attendance", "student_id": str(student_b_id),
                "attendance_date": "2026-10-21", "status": "present",
            },
        ):
            response = self.client.post("/academics", data=payload, follow_redirects=True)
            self.assertEqual(response.status_code, 200)

        self.client.post("/logout")
        self.login_as("finance_route_test")
        finance_page = self.client.get("/student-finances")
        self.assertEqual(finance_page.status_code, 200)
        self.assertNotIn(b"School B Fictional Student", finance_page.data)

        self.client.post("/logout")
        self.login_as("teacher_route_test")
        report_page = self.client.get(
            "/report-cards?student_id=" + str(student_b_id) +
            "&academic_year=2026-2027&term=Term%201"
        )
        self.assertEqual(report_page.status_code, 200)
        self.assertNotIn(b"School B Fictional Student", report_page.data)

        connection = portal.get_db_connection()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM grades WHERE student_id = ? AND subject = 'Unauthorized Subject'",
                    (student_b_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM attendance WHERE student_id = ? AND attendance_date = '2026-10-21'",
                    (student_b_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            connection.close()

    def test_school_a_cannot_publish_school_b_report_card(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES ('School B Test', 'school-b-test', 'active') "
                "ON CONFLICT(slug) DO NOTHING"
            )
            school_b_id = connection.execute(
                "SELECT id FROM schools WHERE slug = 'school-b-test'"
            ).fetchone()["id"]
            teacher_b_id = connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, status, school_id)
                   VALUES ('teacher_school_b', ?, 'teacher', 'active', ?)""",
                (generate_password_hash("test-password-for-route"), school_b_id),
            ).lastrowid
            student_b_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('SCHOOL-B-REPORT-001', 'School B Report Student', 'Grade 8', ?)""",
                (school_b_id,),
            ).lastrowid
            connection.execute(
                """INSERT INTO grades
                   (student_id, subject, assessment, score, max_score, recorded_by,
                    academic_year, term, school_id)
                   VALUES (?, 'Mathematics', 'Quiz 1', 8, 10, ?,
                           '2026-2027', 'Term 1', ?)""",
                (student_b_id, teacher_b_id, school_b_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as("principal_route_test")
        response = self.client.post("/report-cards", data={
            "action": "publish", "student_id": str(student_b_id),
            "academic_year": "2026-2027", "term": "Term 1",
        }, follow_redirects=True)
        self.assertIn(b"Select a valid student, academic year, and term", response.data)

        connection = portal.get_db_connection()
        try:
            published = connection.execute(
                "SELECT published FROM report_cards WHERE student_id = ? AND school_id = ?",
                (student_b_id, school_b_id),
            ).fetchone()
            self.assertIsNone(published)
        finally:
            connection.close()




    def test_platform_admin_cannot_link_student_to_account_from_another_school(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES ('School B Test', 'school-b-test', 'active') "
                "ON CONFLICT(slug) DO NOTHING"
            )
            school_b_id = connection.execute(
                "SELECT id FROM schools WHERE slug = 'school-b-test'"
            ).fetchone()["id"]
            student_b_id = connection.execute(
                """INSERT INTO students (admission_number, full_name, class_level, school_id)
                   VALUES ('SCHOOL-B-LINK-001', 'School B Link Student', 'Grade 8', ?)""",
                (school_b_id,),
            ).lastrowid
            student_account_a_id = connection.execute(
                "SELECT id FROM users WHERE username = 'student_route_test'"
            ).fetchone()["id"]
            connection.commit()
        finally:
            connection.close()

        self.login_as("super_route_test")
        response = self.client.post("/admin/users", data={
            "action": "link_student",
            "user_id": str(student_account_a_id),
            "student_id": str(student_b_id),
            "verify_identity": "yes",
        }, follow_redirects=True)
        self.assertIn(b"Choose a valid active student record", response.data)

        connection = portal.get_db_connection()
        try:
            link = connection.execute(
                "SELECT account_user_id FROM students WHERE id = ? AND school_id = ?",
                (student_b_id, school_b_id),
            ).fetchone()
            self.assertIsNone(link["account_user_id"])
        finally:
            connection.close()


    def test_suspended_school_blocks_existing_sessions_and_new_logins(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO schools (name, slug, status) VALUES ('Suspension Test School', 'suspension-test-school', 'active') "
                "ON CONFLICT(slug) DO NOTHING"
            )
            school_id = connection.execute(
                "SELECT id FROM schools WHERE slug = 'suspension-test-school'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO users
                   (username, password_hash, role, status, school_id)
                   VALUES ('teacher_suspended_school', ?, 'teacher', 'active', ?)""",
                (generate_password_hash("test-password-for-route"), school_id),
            )
            connection.commit()
        finally:
            connection.close()

        self.login_as("teacher_suspended_school")
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Dashboard Overview", dashboard.data)

        connection = portal.get_db_connection()
        try:
            connection.execute("UPDATE schools SET status = 'suspended' WHERE id = ?", (school_id,))
            connection.commit()
        finally:
            connection.close()

        # The next request reloads the account from the database, invalidating the
        # existing Flask-Login session when its school is suspended.
        response = self.client.get("/dashboard", follow_redirects=True)
        self.assertIn(b"Portal Login", response.data)
        self.assertNotIn(b"Dashboard Overview", response.data)

        # A suspended-school account cannot immediately establish a new session.
        response = self.client.post(
            "/login",
            data={"username": "teacher_suspended_school", "password": "test-password-for-route"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid username or password", response.data)
        self.assertNotIn(b"Dashboard Overview", response.data)

class StartupMigrationTests(unittest.TestCase):
    def test_legacy_database_migrates_without_losing_records_and_is_idempotent(self):
        """Exercise the real startup migration on a disposable legacy SQLite file."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            legacy_path = Path(temporary_directory) / "legacy.sqlite3"
            original_database = portal.DATABASE
            try:
                connection = sqlite3.connect(legacy_path)
                connection.execute("PRAGMA foreign_keys = ON")
                connection.executescript(
                    """
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
                        VALUES (10, 'legacy_teacher', 'legacy-hash', 'teacher');
                    INSERT INTO users (id, username, password_hash, role)
                        VALUES (11, 'legacy_student', 'legacy-hash', 'student');
                    INSERT INTO users (id, username, password_hash, role)
                        VALUES (12, 'legacy_admin', 'legacy-hash', 'admin');
                    INSERT INTO students
                        (id, admission_number, full_name, class_level, date_of_birth,
                         guardian_name, guardian_phone)
                        VALUES (20, 'LEGACY-001', 'Fictional Legacy Student', 'Grade 7',
                                '2012-05-04', 'Fictional Guardian', '+231 770 000 000');
                    INSERT INTO grades
                        (id, student_id, subject, assessment, score, max_score, recorded_by)
                        VALUES (30, 20, 'Mathematics', 'Quiz 1', 8, 10, 10);
                    """
                )
                connection.commit()
                connection.close()

                portal.DATABASE = legacy_path
                portal.initialize_database()

                connection = portal.get_db_connection()
                try:
                    student = connection.execute(
                        """SELECT id, admission_number, full_name, class_level,
                                  date_of_birth, guardian_name, guardian_phone, school_id
                           FROM students WHERE id = 20"""
                    ).fetchone()
                    self.assertIsNotNone(student)
                    self.assertEqual(
                        tuple(student),
                        (20, "LEGACY-001", "Fictional Legacy Student", "Grade 7",
                         "2012-05-04", "Fictional Guardian", "+231 770 000 000", 1),
                    )
                    grade = connection.execute(
                        """SELECT id, student_id, subject, assessment, score, max_score,
                                  recorded_by, academic_year, term, school_id
                           FROM grades WHERE id = 30"""
                    ).fetchone()
                    self.assertEqual(
                        tuple(grade),
                        (30, 20, "Mathematics", "Quiz 1", 8.0, 10.0, 10,
                         "Unassigned", "Unassigned", 1),
                    )
                    users = connection.execute(
                        "SELECT id, role, is_super_admin, school_id FROM users ORDER BY id"
                    ).fetchall()
                    self.assertEqual(tuple(users[0]), (10, "teacher", 0, 1))
                    self.assertEqual(tuple(users[1]), (11, "student", 0, 1))
                    self.assertEqual(tuple(users[2]), (12, "super_admin", 1, None))
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

                    connection.execute(
                        "INSERT INTO schools (name, slug, status) VALUES (?, ?, 'active')",
                        ("Fictional Second School", "fictional-second-school"),
                    )
                    second_school_id = connection.execute(
                        "SELECT id FROM schools WHERE slug = 'fictional-second-school'"
                    ).fetchone()["id"]
                    connection.execute(
                        """INSERT INTO students
                           (admission_number, full_name, class_level, school_id)
                           VALUES ('LEGACY-001', 'Second Fictional Student', 'Grade 7', ?)""",
                        (second_school_id,),
                    )
                    connection.commit()
                finally:
                    connection.close()

                # A repeat startup must not duplicate or reassign existing rows.
                portal.initialize_database()
                connection = portal.get_db_connection()
                try:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM students").fetchone()[0], 2
                    )
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM grades WHERE id = 30").fetchone()[0], 1
                    )
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """INSERT INTO students
                               (admission_number, full_name, class_level, school_id)
                               VALUES ('LEGACY-001', 'Duplicate Same-School Student', 'Grade 7', 1)"""
                        )
                    connection.rollback()
                finally:
                    connection.close()
            finally:
                portal.DATABASE = original_database


if __name__ == "__main__":
    unittest.main()
