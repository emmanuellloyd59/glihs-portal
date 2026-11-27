"""Security regression tests for scoped RBAC and ABAC authorization."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "authorization-tests-only-secret")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash
import app as portal
from school_network import initialize_school_network_schema
from identity_memberships import initialize_membership_schema
from authorization import initialize_authorization_schema


class ScopedAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = portal.DATABASE
        portal.DATABASE = Path(self.temp_dir.name) / "authorization.sqlite3"
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        portal.initialize_database()
        initialize_school_network_schema(portal.get_db_connection)
        initialize_membership_schema(portal.get_db_connection)
        initialize_authorization_schema(portal.get_db_connection)
        connection = portal.get_db_connection()
        try:
            connection.execute("UPDATE schools SET status='active',lifecycle_status='active' WHERE id=1")
            self.school_b = connection.execute(
                """INSERT INTO schools(name,slug,status,lifecycle_status,created_at)
                   VALUES('Authorization School B','authorization-school-b','active','active',CURRENT_TIMESTAMP)"""
            ).lastrowid
            self.campus_a = connection.execute(
                """INSERT INTO campuses(school_id,code,name,status)
                   VALUES(1,'NORTH','North Campus','active')"""
            ).lastrowid
            self.teacher_id = connection.execute(
                """INSERT INTO users(username,full_name,email,password_hash,role,status,is_super_admin,school_id)
                   VALUES('auth_teacher','Authorization Teacher','auth-teacher@example.test',?,'teacher','active',0,1)""",
                (generate_password_hash("teacher-password-for-tests"),),
            ).lastrowid
            self.admin_id = connection.execute(
                """INSERT INTO users(username,full_name,email,password_hash,role,status,is_super_admin,school_id)
                   VALUES('auth_admin','Authorization Admin','auth-admin@example.test',?,'school_admin','active',0,1)""",
                (generate_password_hash("admin-password-for-tests"),),
            ).lastrowid
            self.other_id = connection.execute(
                """INSERT INTO users(username,full_name,email,password_hash,role,status,is_super_admin,school_id)
                   VALUES('auth_other','Other Tenant','auth-other@example.test',?,'school_admin','active',0,?)""",
                (generate_password_hash("other-password-for-tests"), self.school_b),
            ).lastrowid
            self.teacher_membership = connection.execute(
                """INSERT INTO school_memberships(user_id,school_id,campus_id,role,status,is_primary)
                   VALUES(?,1,?,'teacher','active',1)""",
                (self.teacher_id, self.campus_a),
            ).lastrowid
            connection.execute(
                """INSERT INTO school_memberships(user_id,school_id,role,status,is_primary)
                   VALUES(?,1,'school_admin','active',1)""", (self.admin_id,)
            )
            connection.execute(
                """INSERT INTO school_memberships(user_id,school_id,role,status,is_primary)
                   VALUES(?,?,'school_admin','active',1)""", (self.other_id, self.school_b)
            )
            connection.commit()
        finally:
            connection.close()
        self.teacher = portal.app.test_client()
        self.admin = portal.app.test_client()
        self.other = portal.app.test_client()
        self.login(self.teacher, "auth_teacher", "teacher-password-for-tests")
        self.login(self.admin, "auth_admin", "admin-password-for-tests")
        self.login(self.other, "auth_other", "other-password-for-tests")

    def tearDown(self):
        portal.DATABASE = self.original_database
        self.temp_dir.cleanup()

    @staticmethod
    def login(client, username, password):
        response = client.post("/login", data={"username": username, "password": password})
        if response.status_code not in (200, 302):
            raise AssertionError("test account could not authenticate")

    def permissions(self, client):
        response = client.get("/api/v1/authorization/me")
        self.assertEqual(response.status_code, 200)
        return set(response.get_json()["permissions"])

    def test_role_defaults_are_least_privilege(self):
        permissions = self.permissions(self.teacher)
        self.assertIn("learning.view", permissions)
        self.assertIn("academic.view_records", permissions)
        self.assertNotIn("school.manage_staff", permissions)
        self.assertNotIn("finance.refund_payments", permissions)
        self.assertNotIn("platform.manage_schools", permissions)

    def test_finance_officer_can_record_payments_but_cannot_refund_or_manage_staff(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE school_memberships SET role='finance_officer' WHERE id=?",
                (self.teacher_membership,),
            )
            connection.commit()
        finally:
            connection.close()
        permissions = self.permissions(self.teacher)
        self.assertIn("finance.record_payments", permissions)
        self.assertIn("finance.view", permissions)
        self.assertNotIn("finance.refund_payments", permissions)
        self.assertNotIn("school.manage_staff", permissions)

    def test_student_self_service_does_not_grant_schoolwide_academic_access(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE school_memberships SET role='student' WHERE id=?",
                (self.teacher_membership,),
            )
            connection.commit()
        finally:
            connection.close()
        permissions = self.permissions(self.teacher)
        self.assertIn("student.view_own", permissions)
        self.assertIn("learning.submit_work", permissions)
        self.assertNotIn("academic.view_records", permissions)
        self.assertNotIn("school.manage_staff", permissions)

    def test_horizontal_privilege_escalation_across_tenants_is_denied(self):
        response = self.teacher.get(
            f"/api/v1/schools/{self.school_b}/configuration"
        )
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn("school.manage_staff", self.permissions(self.teacher))

    def test_vertical_privilege_escalation_staff_admin_route_is_denied(self):
        response = self.teacher.get("/staff")
        self.assertEqual(response.status_code, 403)

    def test_explicit_deny_overrides_inherited_role_allow(self):
        connection = portal.get_db_connection()
        try:
            connection.execute("""
                INSERT INTO authorization_grants
                (subject_type,subject_id,permission,scope_type,scope_id,effect)
                VALUES('user',?,'learning.view','school',1,'deny')
            """, (str(self.teacher_id),))
            connection.commit()
        finally:
            connection.close()
        self.assertNotIn("learning.view", self.permissions(self.teacher))

    def test_inactive_membership_revokes_authorization_on_next_request(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE school_memberships SET status='suspended' WHERE id=?",
                (self.teacher_membership,),
            )
            connection.commit()
        finally:
            connection.close()
        response = self.teacher.get("/api/v1/authorization/me")
        self.assertIn(response.status_code, (302, 401, 403))

    def test_unauthorized_user_cannot_change_role_permission_sets(self):
        response = self.teacher.put(
            "/api/v1/authorization/roles/teacher/permissions",
            json={"permission": "school.manage_staff", "scope_type": "school", "effect": "allow"},
        )
        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_create_grant_for_another_school(self):
        response = self.admin.post("/api/v1/authorization/grants", json={
            "subject_type": "role", "subject_id": "teacher",
            "permission": "school.manage_staff", "scope_type": "school",
            "scope_id": self.school_b, "school_id": self.school_b, "effect": "allow",
        })
        self.assertIn(response.status_code, (403, 404))

    def test_school_admin_cannot_delegate_membership_administration(self):
        response = self.admin.post("/api/v1/authorization/grants", json={
            "subject_type": "role", "subject_id": "teacher",
            "permission": "school.manage_memberships", "scope_type": "school",
            "scope_id": 1, "school_id": 1, "effect": "allow",
        })
        self.assertEqual(response.status_code, 403)

    def test_campus_membership_cannot_be_used_for_another_campus(self):
        connection = portal.get_db_connection()
        try:
            campus_b = connection.execute(
                "INSERT INTO campuses(school_id,code,name,status) VALUES(1,'SOUTH','South Campus','active')"
            ).lastrowid
            connection.execute(
                "UPDATE school_memberships SET role='external_service_provider' WHERE id=?",
                (self.teacher_membership,),
            )
            connection.commit()
            from authorization import has_permission
            with self.teacher:
                self.teacher.get("/api/v1/authorization/me")
                allowed_own = has_permission(connection, "provider.access_assigned_services",
                                              school_id=1, campus_id=self.campus_a)
                allowed_other = has_permission(connection, "provider.access_assigned_services",
                                                school_id=1, campus_id=campus_b)
            self.assertTrue(allowed_own)
            self.assertFalse(allowed_other)
        finally:
            connection.close()

    def test_organization_scope_permission_inherits_to_member_school(self):
        connection = portal.get_db_connection()
        try:
            group_id = connection.execute(
                "INSERT INTO school_groups(name,slug,status) VALUES('Test Education Group','test-education-group','active')"
            ).lastrowid
            connection.execute(
                "INSERT INTO school_group_memberships(group_id,school_id) VALUES(?,1)",
                (group_id,),
            )
            connection.execute("""
                INSERT INTO authorization_grants
                (subject_type,subject_id,permission,scope_type,scope_id,effect)
                VALUES('user',?,'school.manage_roles','organization',?,'allow')
            """, (str(self.teacher_id), group_id))
            connection.commit()
            from authorization import has_permission
            with self.teacher:
                self.teacher.get("/api/v1/authorization/me")
                allowed = has_permission(connection, "school.manage_roles", school_id=1)
            self.assertTrue(allowed)
        finally:
            connection.close()

    def test_permission_decisions_are_audited(self):
        self.assertEqual(self.teacher.get("/staff").status_code, 403)
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM authorization_audit WHERE actor_user_id=?",
                (self.teacher_id,),
            ).fetchone()
            self.assertGreater(row["total"], 0)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
