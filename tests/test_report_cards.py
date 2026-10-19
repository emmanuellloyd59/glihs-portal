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

    def test_principal_can_publish_and_unpublish_report_card(self):
        client = self.login_as("test_principal")
        connection = get_db_connection()
        try:
            student_id = connection.execute(
                "SELECT id FROM students WHERE admission_number='DEMO-001'"
            ).fetchone()["id"]
        finally:
            connection.close()

        payload = {
            "student_id": str(student_id),
            "academic_year": "2026-2027",
            "term": "Term 1",
        }
        published = client.post("/report-cards", data={**payload, "action": "publish"})
        self.assertEqual(published.status_code, 302)
        connection = get_db_connection()
        try:
            row = connection.execute(
                "SELECT published, published_by FROM report_cards WHERE student_id=? AND academic_year=? AND term=?",
                (student_id, "2026-2027", "Term 1"),
            ).fetchone()
            self.assertEqual(row["published"], 1)
            self.assertIsNotNone(row["published_by"])
        finally:
            connection.close()

        unpublished = client.post("/report-cards", data={**payload, "action": "unpublish"})
        self.assertEqual(unpublished.status_code, 302)
        connection = get_db_connection()
        try:
            row = connection.execute(
                "SELECT published, published_at FROM report_cards WHERE student_id=? AND academic_year=? AND term=?",
                (student_id, "2026-2027", "Term 1"),
            ).fetchone()
            self.assertEqual(row["published"], 0)
            self.assertIsNone(row["published_at"])
        finally:
            connection.close()

    def test_report_card_cannot_be_published_without_term_grades(self):
        connection = get_db_connection()
        try:
            connection.execute(
                "INSERT INTO students (admission_number, full_name, class_level) VALUES (?, ?, ?)",
                ("NO-GRADES-001", "No Grades Example", "Grade 8"),
            )
            student_id = connection.execute(
                "SELECT id FROM students WHERE admission_number='NO-GRADES-001'"
            ).fetchone()["id"]
            connection.commit()
        finally:
            connection.close()

        response = self.login_as("test_principal").post("/report-cards", data={
            "student_id": str(student_id), "academic_year": "2026-2027",
            "term": "Term 1", "action": "publish",
        }, follow_redirects=True)
        self.assertIn(b"only be published after grades have been recorded", response.data)
        connection = get_db_connection()
        try:
            row = connection.execute(
                "SELECT id FROM report_cards WHERE student_id=? AND academic_year=? AND term=?",
                (student_id, "2026-2027", "Term 1"),
            ).fetchone()
            self.assertIsNone(row)
        finally:
            connection.close()

    def test_invalid_academic_year_cannot_publish_report_card(self):
        connection = get_db_connection()
        try:
            student_id = connection.execute(
                "SELECT id FROM students WHERE admission_number='DEMO-001'"
            ).fetchone()["id"]
        finally:
            connection.close()
        response = self.login_as("test_principal").post("/report-cards", data={
            "student_id": str(student_id), "academic_year": "2026-2028",
            "term": "Term 1", "action": "publish",
        }, follow_redirects=True)
        self.assertIn(b"Select a valid student, academic year, and term", response.data)

    def test_published_report_card_blocks_new_grade_entries(self):
        connection = get_db_connection()
        try:
            student_id = connection.execute(
                "SELECT id FROM students WHERE admission_number='DEMO-001'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO report_cards
                   (student_id, academic_year, term, published, published_at)
                   VALUES (?, '2026-2027', 'Term 1', 1, CURRENT_TIMESTAMP)
                   ON CONFLICT(student_id, academic_year, term)
                   DO UPDATE SET published=1, published_at=CURRENT_TIMESTAMP""",
                (student_id,),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.login_as("test_teacher").post("/academics", data={
            "action": "grade", "student_id": str(student_id), "subject": "Mathematics",
            "assessment": "Post-Publication Quiz", "score": "9", "max_score": "10",
            "academic_year": "2026-2027", "term": "Term 1",
        }, follow_redirects=True)
        self.assertIn(b"report card is published", response.data)
        connection = get_db_connection()
        try:
            row = connection.execute(
                """SELECT id FROM grades WHERE student_id=? AND subject='Mathematics'
                   AND assessment='Post-Publication Quiz' AND academic_year='2026-2027' AND term='Term 1'""",
                (student_id,),
            ).fetchone()
            self.assertIsNone(row)
        finally:
            connection.close()

    def test_same_assessment_label_is_allowed_in_different_terms(self):
        connection = get_db_connection()
        try:
            student_id = connection.execute(
                "SELECT id FROM students WHERE admission_number='DEMO-001'"
            ).fetchone()["id"]
        finally:
            connection.close()
        response = self.login_as("test_teacher").post("/academics", data={
            "action": "grade", "student_id": str(student_id), "subject": "Mathematics",
            "assessment": "Test Quiz", "score": "9", "max_score": "10",
            "academic_year": "2026-2027", "term": "Term 2",
        }, follow_redirects=True)
        self.assertIn(b"Grade saved successfully", response.data)

    def test_legacy_grade_constraint_migrates_real_old_schema_and_preserves_links(self):
        import sqlite3
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE students (id INTEGER PRIMARY KEY)")
            connection.execute(
                """CREATE TABLE grades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    subject TEXT NOT NULL,
                    assessment TEXT NOT NULL,
                    score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                    max_score REAL NOT NULL CHECK (max_score > 0 AND score <= max_score),
                    recorded_by INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, subject, assessment)
                )"""
            )
            connection.execute("INSERT INTO users (id) VALUES (1)")
            connection.execute("INSERT INTO students (id) VALUES (1)")
            connection.execute(
                """INSERT INTO grades
                   (id, student_id, subject, assessment, score, max_score, recorded_by)
                   VALUES (17, 1, 'Mathematics', 'Quiz 1', 8, 10, 1)"""
            )
            connection.commit()

            # Match initialize_database(): disable FK enforcement for the schema
            # migration, add term columns to the old table, then rebuild its constraint.
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(
                "ALTER TABLE grades ADD COLUMN academic_year TEXT NOT NULL DEFAULT 'Unassigned'"
            )
            connection.execute(
                "ALTER TABLE grades ADD COLUMN term TEXT NOT NULL DEFAULT 'Unassigned'"
            )
            __import__("app").migrate_grade_assessment_uniqueness(connection)
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")

            saved = connection.execute(
                "SELECT id, score, academic_year, term FROM grades WHERE id=17"
            ).fetchone()
            self.assertEqual(
                (saved["id"], saved["score"], saved["academic_year"], saved["term"]),
                (17, 8, "Unassigned", "Unassigned"),
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            # The same assessment can now be recorded in separate terms.
            for term in ("Term 1", "Term 2"):
                connection.execute(
                    """INSERT INTO grades
                       (student_id, subject, assessment, score, max_score, recorded_by, academic_year, term)
                       VALUES (1, 'Mathematics', 'Quiz 1', 9, 10, 1, '2026-2027', ?)""",
                    (term,),
                )
            connection.commit()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM grades").fetchone()[0], 3)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
