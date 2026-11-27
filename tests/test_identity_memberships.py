"""Regression tests for global identities and tenant-scoped school memberships."""
import hashlib
import os
import re
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "identity-membership-tests-only-secret")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash, check_password_hash
import app as portal
from school_network import initialize_school_network_schema
from identity_memberships import initialize_membership_schema


class MultiSchoolIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = portal.DATABASE
        portal.DATABASE = Path(self.temp_dir.name) / "identity.sqlite3"
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        portal.initialize_database()
        initialize_school_network_schema(portal.get_db_connection)
        initialize_membership_schema(portal.get_db_connection)
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE schools SET status='active',lifecycle_status='active' WHERE id=1"
            )
            self.school_b = connection.execute(
                """INSERT INTO schools(name,slug,status,lifecycle_status,created_at)
                   VALUES('Identity School B','identity-school-b','active','active',CURRENT_TIMESTAMP)"""
            ).lastrowid
            self.original_hash = generate_password_hash("correct-horse-battery-staple")
            self.user_a = connection.execute(
                """INSERT INTO users(username,full_name,email,password_hash,role,status,is_super_admin,school_id)
                   VALUES('multi_identity','Multi Identity','multi@example.test',?,'school_admin','active',0,1)""",
                (self.original_hash,),
            ).lastrowid
            self.user_b = connection.execute(
                """INSERT INTO users(username,full_name,email,password_hash,role,status,is_super_admin,school_id)
                   VALUES('other_identity','Other Identity','other@example.test',?,'teacher','active',0,?)""",
                (generate_password_hash("another-test-password"), self.school_b),
            ).lastrowid
            connection.execute(
                """INSERT INTO school_memberships(user_id,school_id,role,status,is_primary)
                   VALUES(?,1,'school_admin','active',1)""",
                (self.user_a,),
            )
            connection.execute(
                """INSERT INTO school_memberships(user_id,school_id,role,status,is_primary)
                   VALUES(?,?,'teacher','active',1)""",
                (self.user_b, self.school_b),
            )
            connection.commit()
            self.membership_a = connection.execute(
                "SELECT id FROM school_memberships WHERE user_id=? AND school_id=1 AND status='active'",
                (self.user_a,),
            ).fetchone()["id"]
            self.membership_b = connection.execute(
                """INSERT INTO school_memberships(user_id,school_id,role,status,is_primary)
                   VALUES(?,?, 'teacher','active',0)""",
                (self.user_a, self.school_b),
            ).lastrowid
            self.other_membership = connection.execute(
                "SELECT id FROM school_memberships WHERE user_id=? AND school_id=?",
                (self.user_b, self.school_b),
            ).fetchone()["id"]
            connection.commit()
        finally:
            connection.close()
        self.client = portal.app.test_client()
        self.client_two = portal.app.test_client()

    def tearDown(self):
        portal.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def login(self, client=None, username="multi_identity"):
        client = client or self.client
        return client.post("/login", data={
            "username": username, "password": (
                "correct-horse-battery-staple" if username == "multi_identity"
                else "another-test-password"
            ),
        })

    def context(self, client=None):
        response = (client or self.client).get("/api/v1/identity/context")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_legacy_identity_and_password_hash_are_preserved_by_membership_backfill(self):
        connection = portal.get_db_connection()
        try:
            before = connection.execute(
                "SELECT id,password_hash,status FROM users WHERE id=?", (self.user_a,)
            ).fetchone()
            initialize_membership_schema(portal.get_db_connection)
            after = connection.execute(
                "SELECT id,password_hash,status FROM users WHERE id=?", (self.user_a,)
            ).fetchone()
            self.assertEqual(before["id"], after["id"])
            self.assertEqual(before["password_hash"], after["password_hash"])
            self.assertEqual(before["status"], after["status"])
            self.assertTrue(check_password_hash(after["password_hash"], "correct-horse-battery-staple"))
        finally:
            connection.close()

    def test_one_identity_can_switch_between_two_schools_with_different_roles(self):
        self.login()
        self.assertEqual(self.context()["school_id"], 1)
        self.assertEqual(self.context()["role"], "school_admin")
        switched = self.client.post(
            "/api/v1/identity/memberships/switch",
            json={"membership_id": self.membership_b},
        )
        self.assertEqual(switched.status_code, 200)
        active = self.context()
        self.assertEqual(active["school_id"], self.school_b)
        self.assertEqual(active["role"], "teacher")
        self.assertEqual(active["membership_id"], self.membership_b)

    def test_knowing_another_membership_identifier_does_not_grant_access(self):
        self.login()
        denied = self.client.post(
            "/api/v1/identity/memberships/switch",
            json={"membership_id": self.other_membership},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.context()["school_id"], 1)

    def test_revoked_or_inactive_membership_cannot_be_selected(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE school_memberships SET status='revoked' WHERE id=?",
                (self.membership_b,),
            )
            connection.commit()
        finally:
            connection.close()
        self.login()
        denied = self.client.post(
            "/api/v1/identity/memberships/switch",
            json={"membership_id": self.membership_b},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.context()["school_id"], 1)

    def test_membership_listing_only_returns_the_authenticated_users_memberships(self):
        self.login()
        response = self.client.get("/api/v1/identity/memberships")
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.get_json()["memberships"]}
        self.assertEqual(ids, {self.membership_a, self.membership_b})
        self.assertNotIn(self.other_membership, ids)

    def test_two_sessions_for_one_identity_keep_independent_school_context(self):
        self.login(self.client)
        self.login(self.client_two)
        response = self.client.post(
            "/api/v1/identity/memberships/switch",
            json={"membership_id": self.membership_b},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.context(self.client)["school_id"], self.school_b)
        self.assertEqual(self.context(self.client_two)["school_id"], 1)

    def test_invitation_acceptance_creates_verified_membership_and_identity(self):
        self.login(self.client)
        sent = {}
        def capture_email(recipient, subject, body):
            sent["recipient"] = recipient
            sent["body"] = body
            return True
        with patch("identity_memberships._send_security_email", side_effect=capture_email):
            created = self.client.post("/api/v1/identity/invitations", json={
                "email": "new.teacher@example.test",
                "role": "teacher",
            })
        self.assertEqual(created.status_code, 201)
        token_match = re.search(r"token=([A-Za-z0-9_-]+)", sent["body"])
        self.assertIsNotNone(token_match)
        invitee = portal.app.test_client()
        accepted = invitee.post("/identity/invitations/accept", json={
            "token": token_match.group(1),
            "username": "invited_teacher",
            "full_name": "Invited Teacher",
            "password": "a-long-invitation-password",
        })
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["status"], "accepted")
        self.assertEqual(invitee.post("/login", data={
            "username": "invited_teacher",
            "password": "a-long-invitation-password",
        }).status_code, 302)
        context = self.context(invitee)
        self.assertEqual(context["school_id"], 1)
        self.assertEqual(context["role"], "teacher")

    def test_password_recovery_invalidates_previously_authenticated_sessions(self):
        self.login(self.client)
        token = "recovery-token-value-that-is-long-enough-123456"
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "INSERT INTO identity_recovery_tokens(user_id,token_hash,expires_at) VALUES(?,?,?)",
                (self.user_a, hashlib.sha256(token.encode()).hexdigest(),
                 (portal.datetime.now(portal.timezone.utc) + portal.timedelta(minutes=30)).isoformat()),
            )
            connection.commit()
        finally:
            connection.close()
        recovery_client = portal.app.test_client()
        reset = recovery_client.post("/identity/recovery/reset", json={
            "token": token,
            "password": "brand-new-recovery-password",
        })
        self.assertEqual(reset.status_code, 200)
        invalidated = self.client.get("/api/v1/identity/context")
        self.assertEqual(invalidated.status_code, 302)
        signed_in = self.client.post("/login", data={
            "username": "multi_identity",
            "password": "brand-new-recovery-password",
        })
        self.assertEqual(signed_in.status_code, 302)
        self.assertEqual(self.context(self.client)["school_id"], 1)


if __name__ == "__main__":
    unittest.main()
