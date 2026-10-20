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
        for table in ("grades", "attendance", "report_cards", "student_finances", "audit_log", "students", "users"):
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
                """INSERT INTO users (username, password_hash, role, status, is_super_admin, school_id)
                   VALUES (?, ?, ?, 'active', ?, 1)""",
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
        academic_page = self.client.get("/academics")
        self.assertEqual(academic_page.status_code, 200)
        self.assertNotIn(b"School B Fictional Student", academic_page.data)
        self.assertNotIn(b"Private Mathematics", academic_page.data)

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

        self.login_as("super_route_test")
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


if __name__ == "__main__":
    unittest.main()
