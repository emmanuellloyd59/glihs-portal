"""International onboarding regression tests on an isolated SQLite database."""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "onboarding-tests-only-secret")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402
from school_network import initialize_school_network_schema  # noqa: E402
from school_onboarding import initialize_onboarding_schema  # noqa: E402


class InternationalOnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_database = portal.DATABASE
        portal.DATABASE = Path(cls.temp_dir.name) / "international_onboarding.sqlite3"
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        portal.initialize_database()
        initialize_school_network_schema(portal.get_db_connection)
        initialize_onboarding_schema(portal.get_db_connection)

    @classmethod
    def tearDownClass(cls):
        portal.DATABASE = cls.original_database
        cls.temp_dir.cleanup()

    def setUp(self):
        self.client = portal.app.test_client()
        connection = portal.get_db_connection()
        try:
            for table in ("onboarding_notification_outbox", "school_admin_invitations",
                          "school_service_requests", "onboarding_events", "onboarding_applications",
                          "onboarding_rate_limits", "country_requirement_profiles",
                          "school_contacts", "campuses", "school_group_memberships",
                          "school_network_audit", "school_groups"):
                connection.execute("DELETE FROM " + table)
            connection.execute("DELETE FROM users")
            connection.execute("DELETE FROM schools WHERE id<>1")
            connection.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES ('platform-owner','Platform Owner',?,'super_admin','active',1,NULL)""",
                (generate_password_hash("platform-owner-test-password"),),
            )
            connection.commit()
        finally:
            connection.close()
        initialize_onboarding_schema(portal.get_db_connection)
        with self.client.session_transaction() as session:
            session.clear()

    def login_owner(self):
        return self.client.post("/login", data={
            "username": "platform-owner", "password": "platform-owner-test-password"
        })

    def payload(self, **overrides):
        data = {
            "name": "International Test School",
            "legal_name": "International Test School Ltd",
            "country_code": "GH",
            "city": "Accra",
            "region": "Greater Accra",
            "county": "Greater Accra",
            "institution_type": "independent",
            "classification": "secondary",
            "ownership_type": "private",
            "owner_name": "Test Education Trust",
            "registration_number": "GH-TEST-2026-001",
            "contact_email": "office@example.test",
            "contact_phone": "+233200000000",
            "administrator_name": "Amina Test",
            "administrator_email": "admin@example.test",
            "requested_services": ["school_website", "school_management", "learning_management"],
            "website_url": "https://school.example.test",
        }
        data.update(overrides)
        return data

    def create_and_verify_draft(self, payload=None):
        payload = payload or self.payload()
        response = self.client.post("/api/v1/onboarding/applications", json=payload)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        data = response.get_json()["data"]
        connection = portal.get_db_connection()
        try:
            outbox = connection.execute(
                "SELECT body FROM onboarding_notification_outbox WHERE recipient=? ORDER BY id DESC LIMIT 1",
                (payload.get("contact_email") or payload.get("administrator_email"),),
            ).fetchone()
            verification_token = outbox["body"].split("one-time token: ", 1)[1].splitlines()[0]
        finally:
            connection.close()
        verified = self.client.post(
            "/api/v1/onboarding/applications/" + data["reference"] + "/verify-email",
            json={"verification_token": verification_token},
        )
        self.assertEqual(verified.status_code, 200)
        return data

    def test_draft_is_resumable_and_requires_email_verification(self):
        payload = self.payload()
        created = self.client.post("/api/v1/onboarding/applications", json=payload)
        self.assertEqual(created.status_code, 201)
        draft = created.get_json()["data"]
        self.assertNotIn("verification_token", draft)
        denied = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/submit",
            json={"resume_token": draft["resume_token"]},
        )
        self.assertEqual(denied.status_code, 403)
        connection = portal.get_db_connection()
        try:
            outbox = connection.execute(
                "SELECT body FROM onboarding_notification_outbox WHERE recipient='office@example.test'"
            ).fetchone()
            verify_token = outbox["body"].split("one-time token: ", 1)[1].splitlines()[0]
        finally:
            connection.close()
        verified = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/verify-email",
            json={"verification_token": verify_token},
        )
        self.assertEqual(verified.status_code, 200)
        resumed = self.client.get(
            "/api/v1/onboarding/applications/" + draft["reference"],
            headers={"X-Onboarding-Token": draft["resume_token"]},
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.get_json()["data"]["status"], "draft")
        changed = self.payload(city="Kumasi")
        changed.pop("contact_email")
        changed["resume_token"] = draft["resume_token"]
        updated = self.client.put(
            "/api/v1/onboarding/applications/" + draft["reference"], json=changed
        )
        self.assertEqual(updated.status_code, 200)

    def test_changing_applicant_email_revokes_verification_and_requires_new_token(self):
        draft = self.create_and_verify_draft()
        updated = self.client.put(
            "/api/v1/onboarding/applications/" + draft["reference"],
            json={"resume_token":draft["resume_token"],"contact_email":"changed@example.test"},
        )
        self.assertEqual(updated.status_code,200)
        denied = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/submit",
            json={"resume_token":draft["resume_token"]},
        )
        self.assertEqual(denied.status_code,403)
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT body FROM onboarding_notification_outbox WHERE recipient='changed@example.test' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            token = row["body"].split("one-time token: ",1)[1].splitlines()[0]
        finally:
            connection.close()
        verified = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/verify-email",
            json={"verification_token":token},
        )
        self.assertEqual(verified.status_code,200)
        submitted = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/submit",
            json={"resume_token":draft["resume_token"]},
        )
        self.assertEqual(submitted.status_code,202)

    def test_registration_wizard_is_public_and_country_options_are_available(self):
        page = self.client.get("/school-network/onboarding")
        self.assertEqual(page.status_code,200)
        countries = self.client.get("/api/v1/onboarding/countries")
        self.assertEqual(countries.status_code,200)
        result = countries.get_json()["data"]
        self.assertIn("LR",{row["country_code"] for row in result["countries"]})
        self.assertIn("GH",{row["country_code"] for row in result["countries"]})
        self.assertIn("school_management",{row["service_key"] for row in result["services"]})

    def test_submission_creates_pending_school_and_requested_services(self):
        draft = self.create_and_verify_draft()
        response = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/submit",
            json={"resume_token": draft["resume_token"]},
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        result = response.get_json()["data"]
        self.assertEqual(result["lifecycle_status"], "pending")
        connection = portal.get_db_connection()
        try:
            school = connection.execute(
                "SELECT * FROM schools WHERE id=?", (result["school_id"],)
            ).fetchone()
            services = connection.execute(
                "SELECT service_key FROM school_service_requests WHERE school_id=? ORDER BY service_key",
                (result["school_id"],),
            ).fetchall()
            event = connection.execute(
                "SELECT event_type FROM onboarding_events WHERE school_id=? ORDER BY id DESC LIMIT 1",
                (result["school_id"],),
            ).fetchone()
            self.assertEqual(school["country_code"], "GH")
            self.assertEqual(school["lifecycle_status"], "pending")
            self.assertEqual(school["status"], "suspended")
            self.assertEqual({row["service_key"] for row in services},
                             {"school_website", "school_management", "learning_management"})
            self.assertEqual(event["event_type"], "application.submitted")
        finally:
            connection.close()

    def test_country_specific_requirements_are_configurable(self):
        self.login_owner()
        configured = self.client.put("/api/v1/onboarding/countries/KE", json={
            "country_name": "Kenya",
            "region_label": "County",
            "region_required": True,
            "registration_number_required": True,
            "requirements": {"required_fields": ["city", "owner_name", "contact_phone"]},
        })
        self.assertEqual(configured.status_code, 200, configured.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/v1/onboarding/countries").status_code, 200)
        invalid_payload = self.payload(country_code="KE", region="", registration_number="",
                                       city="", owner_name="", contact_phone="")
        draft = self.client.post("/api/v1/onboarding/applications", json=invalid_payload)
        self.assertEqual(draft.status_code, 201)
        data = draft.get_json()["data"]
        connection = portal.get_db_connection()
        try:
            outbox = connection.execute(
                "SELECT body FROM onboarding_notification_outbox WHERE recipient='office@example.test' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            verify_token = outbox["body"].split("one-time token: ", 1)[1].splitlines()[0]
        finally:
            connection.close()
        self.client.post("/api/v1/onboarding/applications/" + data["reference"] + "/verify-email",
                         json={"verification_token": verify_token})
        submitted = self.client.post(
            "/api/v1/onboarding/applications/" + data["reference"] + "/submit",
            json={"resume_token": data["resume_token"]},
        )
        self.assertEqual(submitted.status_code, 422)
        messages = submitted.get_json()["error"]["messages"]
        self.assertTrue(any("County is required" in item for item in messages))
        self.assertTrue(any("registration or license" in item for item in messages))
        self.assertTrue(any("City is required" in item for item in messages))

    def test_duplicate_detection_is_country_scoped_and_non_disclosing(self):
        first = self.create_and_verify_draft()
        first_submit = self.client.post(
            "/api/v1/onboarding/applications/" + first["reference"] + "/submit",
            json={"resume_token": first["resume_token"]},
        )
        self.assertEqual(first_submit.status_code, 202)
        second_payload = self.payload(contact_email="second@example.test",
                                      administrator_email="second-admin@example.test")
        second = self.create_and_verify_draft(second_payload)
        duplicate = self.client.post(
            "/api/v1/onboarding/applications/" + second["reference"] + "/submit",
            json={"resume_token": second["resume_token"]},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()["error"]["code"], "possible_duplicate")

    def test_secure_admin_invitation_requires_active_school_and_creates_scoped_account(self):
        self.login_owner()
        connection = portal.get_db_connection()
        try:
            school_id = connection.execute(
                """INSERT INTO schools(name,slug,status,lifecycle_status,country_code)
                   VALUES ('Invite School','invite-school','active','active','GH')"""
            ).lastrowid
            connection.commit()
        finally:
            connection.close()
        invite_page = self.client.get("/school-network/invitations/accept")
        self.assertEqual(invite_page.status_code,200)
        invite = self.client.post(
            f"/api/v1/onboarding/schools/{school_id}/invite-administrator",
            json={"email":"new-admin@example.test","full_name":"New School Admin"},
        )
        self.assertEqual(invite.status_code, 202, invite.get_data(as_text=True))
        connection = portal.get_db_connection()
        try:
            body = connection.execute(
                "SELECT body FROM onboarding_notification_outbox WHERE recipient='new-admin@example.test' ORDER BY id DESC LIMIT 1"
            ).fetchone()["body"]
            token = body.split("#token=",1)[1].splitlines()[0]
        finally:
            connection.close()
        accepted = self.client.post("/api/v1/onboarding/invitations/accept", json={
            "token":token,"password":"a-strong-test-password-2026"
        })
        self.assertEqual(accepted.status_code, 201, accepted.get_data(as_text=True))
        connection = portal.get_db_connection()
        try:
            user = connection.execute(
                "SELECT role,status,school_id FROM users WHERE username='new-admin@example.test'"
            ).fetchone()
            self.assertEqual((user["role"],user["status"],user["school_id"]),
                             ("school_admin","active",school_id))
        finally:
            connection.close()
        replay = self.client.post("/api/v1/onboarding/invitations/accept", json={
            "token":token,"password":"a-strong-test-password-2026"
        })
        self.assertEqual(replay.status_code, 404)

    def test_end_to_end_verification_approval_activation_and_configuration_stages(self):
        draft = self.create_and_verify_draft()
        submitted = self.client.post(
            "/api/v1/onboarding/applications/" + draft["reference"] + "/submit",
            json={"resume_token":draft["resume_token"]},
        )
        self.assertEqual(submitted.status_code,202)
        school_id = submitted.get_json()["data"]["school_id"]
        self.login_owner()
        stages = []
        stages.append(self.client.get(f"/api/v1/onboarding/schools/{school_id}/progress").get_json()["data"]["stage"])
        for action, expected in (("start_review","approval"),("approve","activation"),("activate","configuration")):
            changed = self.client.post(f"/api/v1/schools/{school_id}/review",json={"action":action})
            self.assertEqual(changed.status_code,200,changed.get_data(as_text=True))
            stages.append(self.client.get(f"/api/v1/onboarding/schools/{school_id}/progress").get_json()["data"]["stage"])
        self.assertEqual(stages,["verification","approval","activation","configuration"])
        invitation = self.client.post(
            f"/api/v1/onboarding/schools/{school_id}/invite-administrator",
            json={"email":"stage-admin@example.test","full_name":"Stage Administrator"},
        )
        self.assertEqual(invitation.status_code,202)
        connection = portal.get_db_connection()
        try:
            body = connection.execute(
                "SELECT body FROM onboarding_notification_outbox WHERE recipient='stage-admin@example.test' ORDER BY id DESC LIMIT 1"
            ).fetchone()["body"]
            token = body.split("#token=",1)[1].splitlines()[0]
        finally:
            connection.close()
        accepted = self.client.post("/api/v1/onboarding/invitations/accept",json={
            "token":token,"password":"stage-admin-strong-password-2026"
        })
        self.assertEqual(accepted.status_code,201)
        with self.client.session_transaction() as session:
            session.clear()
        self.client.post("/login",data={
            "username":"stage-admin@example.test","password":"stage-admin-strong-password-2026"
        })
        completed = self.client.post(
            f"/api/v1/onboarding/schools/{school_id}/configuration/complete"
        )
        self.assertEqual(completed.status_code,200,completed.get_data(as_text=True))
        progress = self.client.get(f"/api/v1/onboarding/schools/{school_id}/progress")
        self.assertEqual(progress.get_json()["data"]["stage"],"completed")

    def test_notifications_remain_queued_without_smtp_and_admin_api_is_protected(self):
        self.assertEqual(self.client.post("/api/v1/onboarding/notifications/dispatch").status_code, 401)
        self.login_owner()
        response = self.client.post("/api/v1/onboarding/notifications/dispatch")
        self.assertEqual(response.status_code, 503)
        connection = portal.get_db_connection()
        try:
            self.assertIsNotNone(connection.execute(
                "SELECT id FROM onboarding_notification_outbox WHERE status='pending' LIMIT 1"
            ).fetchone())
        finally:
            connection.close()

    def test_public_draft_creation_is_rate_limited_per_email(self):
        responses = [
            self.client.post("/api/v1/onboarding/applications",json=self.payload())
            for _ in range(9)
        ]
        self.assertTrue(all(response.status_code == 201 for response in responses[:8]))
        self.assertEqual(responses[8].status_code,429)

    def test_resume_token_is_required_and_honeypot_is_rejected_without_persisting(self):
        created = self.client.post("/api/v1/onboarding/applications", json=self.payload())
        reference = created.get_json()["data"]["reference"]
        self.assertEqual(self.client.get("/api/v1/onboarding/applications/" + reference).status_code, 404)
        spam = self.client.post("/api/v1/onboarding/applications", json=self.payload(website="spam.example"))
        self.assertEqual(spam.status_code, 202)
        connection = portal.get_db_connection()
        try:
            count = connection.execute("SELECT COUNT(*) FROM onboarding_applications").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
