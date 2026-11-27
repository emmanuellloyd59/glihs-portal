"""School Network foundation tests using an isolated temporary SQLite database."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "school-network-tests-only-secret")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402
from school_network import initialize_school_network_schema  # noqa: E402


class SchoolNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_database = portal.DATABASE
        portal.DATABASE = Path(cls.temp_dir.name) / "school_network_test.sqlite3"
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        portal.initialize_database()
        initialize_school_network_schema(portal.get_db_connection)

    @classmethod
    def tearDownClass(cls):
        portal.DATABASE = cls.original_database
        cls.temp_dir.cleanup()

    def setUp(self):
        self.client = portal.app.test_client()
        connection = portal.get_db_connection()
        try:
            connection.execute("DELETE FROM school_network_audit")
            connection.execute("DELETE FROM school_contacts")
            connection.execute("DELETE FROM campuses")
            connection.execute("DELETE FROM school_group_memberships")
            connection.execute("DELETE FROM school_groups")
            connection.execute("DELETE FROM schools WHERE id <> 1")
            connection.execute("DELETE FROM users")
            connection.execute(
                """INSERT INTO users
                   (username, full_name, password_hash, role, status, is_super_admin, school_id)
                   VALUES ('network_owner', 'Network Owner', ?, 'super_admin', 'active', 1, NULL)""",
                (generate_password_hash("network-owner-password"),),
            )
            connection.execute(
                """INSERT INTO users
                   (username, full_name, password_hash, role, status, is_super_admin, school_id)
                   VALUES ('school_admin_a', 'School Admin A', ?, 'school_admin', 'active', 0, 1)""",
                (generate_password_hash("school-admin-password"),),
            )
            connection.execute(
                """INSERT INTO users
                   (username, full_name, password_hash, role, status, is_super_admin, school_id)
                   VALUES ('school_admin_b', 'School Admin B', ?, 'school_admin', 'active', 0, 1)""",
                (generate_password_hash("school-admin-password"),),
            )
            connection.commit()
        finally:
            connection.close()
        with self.client.session_transaction() as session:
            session.clear()

    def login(self, username, password):
        return self.client.post("/login", data={"username": username, "password": password})

    def registration_payload(self, slug="test-academy", **overrides):
        payload = {
            "name": "Test Academy",
            "slug": slug,
            "legal_name": "Test Academy Incorporated",
            "institution_type": "independent",
            "classification": "secondary",
            "ownership_type": "private",
            "owner_name": "Fictional Education Trust",
            "registration_number": "TEST-REG-001",
            "website_url": "https://example.edu",
            "description": "Fictional institution for automated tests.",
            "address_line1": "1 Test Road",
            "city": "Monrovia",
            "county": "Montserrado",
            "country_code": "LR",
            "timezone": "Africa/Monrovia",
            "contact_email": "office@example.edu",
            "contact_phone": "+231000000000",
        }
        payload.update(overrides)
        return payload

    def test_migration_is_additive_idempotent_and_preserves_legacy_school(self):
        connection = portal.get_db_connection()
        try:
            before = connection.execute("SELECT id, name, slug, status FROM schools WHERE id=1").fetchone()
            connection.execute(
                "INSERT INTO users (username, password_hash, role, school_id) VALUES (?, ?, 'teacher', 1)",
                ("legacy_teacher", generate_password_hash("legacy-password")),
            )
            connection.commit()
        finally:
            connection.close()

        initialize_school_network_schema(portal.get_db_connection)
        initialize_school_network_schema(portal.get_db_connection)

        connection = portal.get_db_connection()
        try:
            after = connection.execute("SELECT id, name, slug, status, lifecycle_status FROM schools WHERE id=1").fetchone()
            teacher = connection.execute("SELECT username, school_id FROM users WHERE username='legacy_teacher'").fetchone()
            tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual((after["id"], after["name"], after["slug"], after["status"]), tuple(before))
            self.assertEqual(after["lifecycle_status"], "active")
            self.assertEqual((teacher["username"], teacher["school_id"]), ("legacy_teacher", 1))
            self.assertTrue({"school_groups", "school_group_memberships", "campuses", "school_contacts", "school_network_audit"}.issubset(tables))
        finally:
            connection.close()

    def test_public_registration_creates_pending_inactive_institution_and_contact(self):
        response = self.client.post("/api/v1/schools", json=self.registration_payload())
        self.assertEqual(response.status_code, 202)
        result = response.get_json()["data"]
        self.assertEqual(result["lifecycle_status"], "pending")
        connection = portal.get_db_connection()
        try:
            school = connection.execute("SELECT * FROM schools WHERE id=?", (result["id"],)).fetchone()
            contact = connection.execute("SELECT * FROM school_contacts WHERE school_id=?", (result["id"],)).fetchone()
            audit = connection.execute(
                "SELECT action FROM school_network_audit WHERE target_school_id=?", (result["id"],)
            ).fetchone()
            self.assertEqual(school["status"], "suspended")
            self.assertEqual(school["lifecycle_status"], "pending")
            self.assertEqual(school["slug"], "test-academy")
            self.assertEqual(contact["email"], "office@example.edu")
            self.assertEqual(audit["action"], "school.registration_submitted")
        finally:
            connection.close()

    def test_registration_validation_and_duplicate_identity(self):
        invalid = self.registration_payload(slug="BAD SLUG", contact_email="not-an-email")
        response = self.client.post("/api/v1/schools", json=invalid)
        self.assertEqual(response.status_code, 422)
        first = self.client.post("/api/v1/schools", json=self.registration_payload())
        second = self.client.post("/api/v1/schools", json=self.registration_payload())
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)

    def test_platform_owner_must_approve_before_activation_and_can_suspend(self):
        self.login("network_owner", "network-owner-password")
        created = self.client.post("/api/v1/schools", json=self.registration_payload())
        school_id = created.get_json()["data"]["id"]
        approve = self.client.post(f"/api/v1/schools/{school_id}/review", json={"action": "approve"})
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.get_json()["data"]["lifecycle_status"], "approved")
        activate = self.client.post(f"/api/v1/schools/{school_id}/review", json={"action": "activate"})
        self.assertEqual(activate.status_code, 200)
        self.assertEqual(activate.get_json()["data"]["status"], "active")
        suspend = self.client.post(f"/api/v1/schools/{school_id}/review", json={"action": "suspend"})
        self.assertEqual(suspend.status_code, 200)
        self.assertEqual(suspend.get_json()["data"]["lifecycle_status"], "suspended")
        invalid_reactivate = self.client.post(f"/api/v1/schools/{school_id}/review", json={"action": "activate"})
        self.assertEqual(invalid_reactivate.status_code, 409)

    def test_non_platform_user_cannot_list_or_change_institution_lifecycle(self):
        self.login("school_admin_a", "school-admin-password")
        self.assertEqual(self.client.get("/api/v1/schools").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/schools/1/review", json={"action": "suspend"}).status_code, 403)
        self.assertEqual(self.client.get("/platform/schools").status_code, 403)

    def test_school_admin_can_update_own_profile_but_cannot_access_another_school(self):
        connection = portal.get_db_connection()
        try:
            other_id = connection.execute(
                """INSERT INTO schools (name, slug, status, lifecycle_status)
                   VALUES ('Other Academy', 'other-academy', 'active', 'active')"""
            ).lastrowid
            connection.execute("UPDATE users SET school_id=? WHERE username='school_admin_b'", (other_id,))
            connection.commit()
        finally:
            connection.close()
        self.login("school_admin_a", "school-admin-password")
        own = self.client.patch("/api/v1/schools/1", json={"city": "Paynesville"})
        other = self.client.get(f"/api/v1/schools/{other_id}")
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.get_json()["data"]["city"], "Paynesville")
        self.assertEqual(other.status_code, 404)

    def test_campuses_and_contacts_are_scoped_to_their_school(self):
        connection = portal.get_db_connection()
        try:
            other_id = connection.execute(
                """INSERT INTO schools (name, slug, status, lifecycle_status)
                   VALUES ('Other Academy', 'other-academy', 'active', 'active')"""
            ).lastrowid
            connection.execute("UPDATE users SET school_id=? WHERE username='school_admin_b'", (other_id,))
            connection.commit()
        finally:
            connection.close()
        self.login("school_admin_a", "school-admin-password")
        campus = self.client.post("/api/v1/schools/1/campuses", json={
            "code": "MAIN", "name": "Main Campus", "city": "Monrovia", "country_code": "LR", "is_main": True
        })
        self.assertEqual(campus.status_code, 201)
        campus_id = campus.get_json()["data"]["id"]
        self.assertEqual(self.client.get(f"/api/v1/schools/{other_id}/campuses").status_code, 404)
        cross_update = self.client.patch(f"/api/v1/schools/{other_id}/campuses/{campus_id}", json={"status": "suspended"})
        self.assertEqual(cross_update.status_code, 404)
        contact = self.client.post("/api/v1/schools/1/contacts", json={
            "contact_type": "admissions", "full_name": "Fictional Admissions Officer", "email": "admissions@example.edu"
        })
        self.assertEqual(contact.status_code, 201)

    def test_platform_owner_can_create_school_group_and_register_member_school(self):
        self.login("network_owner", "network-owner-password")
        group = self.client.post("/api/v1/school-groups", json={
            "name": "Example Education Group", "slug": "example-education-group",
            "ownership_type": "private", "owner_name": "Example Holdings"
        })
        self.assertEqual(group.status_code, 201)
        group_id = group.get_json()["data"]["id"]
        payload = self.registration_payload(
            slug="group-member-school", institution_type="group_affiliated", group_id=group_id
        )
        registration = self.client.post("/api/v1/schools", json=payload)
        self.assertEqual(registration.status_code, 202)
        connection = portal.get_db_connection()
        try:
            membership = connection.execute(
                "SELECT group_id FROM school_group_memberships WHERE school_id=?",
                (registration.get_json()["data"]["id"],),
            ).fetchone()
            self.assertEqual(membership["group_id"], group_id)
        finally:
            connection.close()

    def test_api_requires_authentication_and_rejects_unknown_actions(self):
        response = self.client.get("/api/v1/schools")
        self.assertEqual(response.status_code, 401)
        self.login("network_owner", "network-owner-password")
        created = self.client.post("/api/v1/schools", json=self.registration_payload())
        school_id = created.get_json()["data"]["id"]
        invalid = self.client.post(f"/api/v1/schools/{school_id}/review", json={"action": "delete"})
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
