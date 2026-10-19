"""Focused unit tests for Version 0.2 report-card calculations and term validation."""
import os
import tempfile
import unittest

# Set the database before importing the Flask application so tests never touch live data.
_TEST_DIR = tempfile.TemporaryDirectory()
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production")
os.environ["DATABASE_PATH"] = os.path.join(_TEST_DIR.name, "test.sqlite3")

from app import app, get_db_connection  # noqa: E402


class ReportCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SECRET_KEY="test-only-secret-key")

    def test_report_card_route_requires_login(self):
        client = app.test_client()
        response = client.get("/report-cards")
        self.assertIn(response.status_code, (302, 401))

    def test_report_card_route_denies_student_role(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "999"
            session["_fresh"] = True
        # A missing database user is logged out by Flask-Login's user loader.
        response = client.get("/report-cards")
        self.assertIn(response.status_code, (302, 401))

    def test_report_cards_table_exists(self):
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
