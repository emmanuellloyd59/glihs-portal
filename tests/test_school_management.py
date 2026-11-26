"""Version 5 school lifecycle, administrator and tenant-isolation tests.

Uses fictional schools and accounts in the repository's disposable test database.
"""
import os
import tempfile
import unittest
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["SECRET_KEY"] = "v5-school-tests-only-secret"
os.environ["DATABASE_PATH"] = str(Path(_TEMP_DIR.name) / "school_management_test.db")
os.environ["SESSION_COOKIE_SECURE"] = "false"

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402


class SchoolManagementTests(unittest.TestCase):
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
        try:
            test_schools = connection.execute(
                "SELECT id FROM schools WHERE slug LIKE 'v5-test-%'"
            ).fetchall()
            test_school_ids = [row["id"] for row in test_schools]
            for school_id in test_school_ids:
                connection.execute("DELETE FROM school_admin_assignments WHERE school_id=?", (school_id,))
                connection.execute("DELETE FROM school_settings WHERE school_id=?", (school_id,))
                connection.execute("DELETE FROM school_academic_calendars WHERE school_id=?", (school_id,))
                connection.execute("DELETE FROM audit_log WHERE school_id=?", (school_id,))
                connection.execute("DELETE FROM users WHERE school_id=?", (school_id,))
                connection.execute("DELETE FROM schools WHERE id=?", (school_id,))
            connection.execute(
                "DELETE FROM audit_log WHERE actor_user_id IN (SELECT id FROM users WHERE username='v5_platform_admin')"
            )
            connection.execute("DELETE FROM users WHERE username='v5_platform_admin'")
            connection.execute(
                """INSERT INTO users
                   (username,password_hash,role,status,is_super_admin,school_id,full_name)
                   VALUES ('v5_platform_admin',?,'super_admin','active',1,NULL,'V5 Platform Admin')""",
                (generate_password_hash("V5-Platform-Admin-Password!"),),
            )
            connection.commit()
        finally:
            connection.close()
        with self.client.session_transaction() as session:
            session.clear()

    def login_as(self, username, password):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def create_school_and_admin(self, slug, username):
        self.client.post("/platform/schools", data={
            "action": "create",
            "name": slug.replace("-", " ").title(),
            "slug": slug,
            "registration_number": "",
            "country_code": "LR",
            "timezone": "Africa/Monrovia",
            "currency_code": "LRD",
            "contact_email": "",
        })
        connection = portal.get_db_connection()
        try:
            school = connection.execute(
                "SELECT id,status FROM schools WHERE slug=?", (slug,)
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(school)
        self.assertEqual(school["status"], "suspended")
        self.client.post("/platform/schools", data={
            "action": "create_admin_account",
            "school_id": str(school["id"]),
            "full_name": username.replace("_", " ").title(),
            "username": username,
            "temporary_password": "V5-Temporary-Password!",
        })
        connection = portal.get_db_connection()
        try:
            admin = connection.execute(
                "SELECT id,status,role,school_id FROM users WHERE username=?",
                (username,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(admin)
        self.assertEqual(admin["role"], "school_admin")
        self.assertEqual(admin["status"], "active")
        self.assertEqual(admin["school_id"], school["id"])
        self.client.post("/platform/schools", data={
            "action": "status", "school_id": str(school["id"]), "status": "active",
        })
        return school["id"], admin["id"]

    def test_school_registration_creates_suspended_school_and_preserves_cmhs(self):
        self.login_as("v5_platform_admin", "V5-Platform-Admin-Password!")
        school_id, admin_id = self.create_school_and_admin("v5-test-campus-a", "v5_admin_a")
        connection = portal.get_db_connection()
        try:
            cmhs = connection.execute(
                "SELECT id,name,slug,status FROM schools WHERE id=1"
            ).fetchone()
            school = connection.execute(
                "SELECT status,registration_number,country_code,timezone,currency_code FROM schools WHERE id=?",
                (school_id,),
            ).fetchone()
            assignment = connection.execute(
                """SELECT active FROM school_admin_assignments
                   WHERE school_id=? AND user_id=?""",
                (school_id, admin_id),
            ).fetchone()
            self.assertEqual((cmhs["id"], cmhs["name"], cmhs["slug"]), (1, "CMHS", "legacy-demo-campus"))
            self.assertEqual(school["status"], "active")
            self.assertEqual(school["country_code"], "LR")
            self.assertEqual(school["timezone"], "Africa/Monrovia")
            self.assertEqual(school["currency_code"], "LRD")
            self.assertEqual(assignment["active"], 1)
        finally:
            connection.close()

    def test_school_admin_can_manage_only_own_profile_settings_and_calendar(self):
        self.login_as("v5_platform_admin", "V5-Platform-Admin-Password!")
        school_a, admin_a = self.create_school_and_admin("v5-test-campus-a", "v5_admin_a")
        school_b, admin_b = self.create_school_and_admin("v5-test-campus-b", "v5_admin_b")

        self.login_as("v5_admin_a", "V5-Temporary-Password!")
        self.assertEqual(self.client.get("/school/settings").status_code, 200)
        self.assertEqual(self.client.get("/school/academic-calendar").status_code, 200)
        self.assertEqual(self.client.get("/platform/schools").status_code, 403)

        self.client.post("/school/settings", data={
            "action": "setting", "setting_key": "academic_year_start_month",
            "setting_value": "8", "school_id": str(school_b),
        })
        self.client.post("/school/academic-calendar", data={
            "action": "create", "academic_year": "2026-2027", "term": "Term 1",
            "start_date": "2026-08-15", "end_date": "2026-12-15", "status": "active",
        })
        connection = portal.get_db_connection()
        try:
            own_setting = connection.execute(
                "SELECT setting_value FROM school_settings WHERE school_id=? AND setting_key='academic_year_start_month'",
                (school_a,),
            ).fetchone()
            foreign_setting = connection.execute(
                "SELECT COUNT(*) FROM school_settings WHERE school_id=? AND setting_key='academic_year_start_month'",
                (school_b,),
            ).fetchone()[0]
            calendar_b = connection.execute(
                """INSERT INTO school_academic_calendars
                   (school_id,academic_year,term,start_date,end_date,status,created_by,updated_by)
                   VALUES (?,'2026-2027','Term 1','2026-08-15','2026-12-15','planned',?,?)""",
                (school_b, admin_b, admin_b),
            ).lastrowid
            connection.commit()
            self.assertEqual(own_setting["setting_value"], "8")
            self.assertEqual(foreign_setting, 0)
        finally:
            connection.close()

        response = self.client.post("/school/academic-calendar", data={
            "action": "update", "calendar_id": str(calendar_b),
            "academic_year": "2026-2027", "term": "Term 1",
            "start_date": "2026-08-15", "end_date": "2026-12-15", "status": "closed",
        })
        self.assertEqual(response.status_code, 404)
        connection = portal.get_db_connection()
        try:
            status = connection.execute(
                "SELECT status FROM school_academic_calendars WHERE id=?", (calendar_b,)
            ).fetchone()["status"]
            self.assertEqual(status, "planned")
        finally:
            connection.close()

    def test_school_calendar_rejects_overlaps_and_multiple_active_terms(self):
        self.login_as("v5_platform_admin", "V5-Platform-Admin-Password!")
        school_id, _ = self.create_school_and_admin("v5-test-campus-a", "v5_admin_a")
        self.login_as("v5_admin_a", "V5-Temporary-Password!")
        self.client.post("/school/academic-calendar", data={
            "action": "create", "academic_year": "2026-2027", "term": "Term 1",
            "start_date": "2026-08-15", "end_date": "2026-12-15", "status": "active",
        })
        self.client.post("/school/academic-calendar", data={
            "action": "create", "academic_year": "2026-2027", "term": "Term 2",
            "start_date": "2026-12-01", "end_date": "2027-03-30", "status": "planned",
        })
        self.client.post("/school/academic-calendar", data={
            "action": "create", "academic_year": "2026-2027", "term": "Term 2",
            "start_date": "2027-01-01", "end_date": "2027-03-30", "status": "active",
        })
        connection = portal.get_db_connection()
        try:
            rows = connection.execute(
                "SELECT term,status FROM school_academic_calendars WHERE school_id=? ORDER BY term",
                (school_id,),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["term"], "Term 1")
            self.assertEqual(rows[0]["status"], "active")
        finally:
            connection.close()

    def test_suspending_school_blocks_existing_school_admin_sessions(self):
        self.login_as("v5_platform_admin", "V5-Platform-Admin-Password!")
        school_id, _ = self.create_school_and_admin("v5-test-campus-a", "v5_admin_a")
        self.client.post("/platform/schools", data={
            "action": "status", "school_id": str(school_id), "status": "suspended",
        })
        self.login_as("v5_admin_a", "V5-Temporary-Password!")
        self.assertEqual(self.client.get("/school/settings").status_code, 403)
        self.assertEqual(self.client.get("/school/academic-calendar").status_code, 403)

    def test_revoked_school_admin_assignment_removes_tenant_access(self):
        self.login_as("v5_platform_admin", "V5-Platform-Admin-Password!")
        school_id, admin_id = self.create_school_and_admin("v5-test-campus-a", "v5_admin_a")
        self.client.post("/platform/schools", data={
            "action": "create_admin_account", "school_id": str(school_id),
            "full_name": "Second V5 Administrator", "username": "v5_admin_b",
            "temporary_password": "V5-Temporary-Password!",
        })
        connection = portal.get_db_connection()
        try:
            assignment = connection.execute(
                """SELECT id FROM school_admin_assignments
                   WHERE school_id=? AND user_id=? AND active=1""",
                (school_id, admin_id),
            ).fetchone()["id"]
        finally:
            connection.close()
        self.client.post("/platform/schools", data={
            "action": "revoke_admin", "school_id": str(school_id),
            "assignment_id": str(assignment),
        })
        self.login_as("v5_admin_a", "V5-Temporary-Password!")
        self.assertEqual(self.client.get("/school/settings").status_code, 403)
        self.assertEqual(self.client.get("/school/academic-calendar").status_code, 403)

    def test_default_cmhs_school_cannot_be_suspended(self):
        self.login_as("v5_platform_admin", "V5-Platform-Admin-Password!")
        self.client.post("/platform/schools", data={
            "action": "status", "school_id": "1", "status": "suspended",
        })
        connection = portal.get_db_connection()
        try:
            status = connection.execute(
                "SELECT status FROM schools WHERE id=1"
            ).fetchone()["status"]
            self.assertEqual(status, "active")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
