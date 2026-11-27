"""School Configuration Centre tenant isolation, migration and versioning tests."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "school-configuration-tests-only-secret")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash
import app as portal
from school_network import initialize_school_network_schema
from school_configuration import initialize_configuration_schema


class SchoolConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = portal.DATABASE
        portal.DATABASE = Path(self.temp_dir.name) / "configuration.sqlite3"
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        portal.initialize_database()
        initialize_school_network_schema(portal.get_db_connection)
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """UPDATE schools SET status='active', lifecycle_status='active',
                   country_code='LR', timezone='Africa/Monrovia',
                   contact_email='office@school-a.example' WHERE id=1"""
            )
            cursor = connection.execute(
                """INSERT INTO schools(name,slug,status,lifecycle_status,country_code,timezone,
                   contact_email,created_at) VALUES
                   ('School B','school-b','active','active','GH','Africa/Accra',
                    'office@school-b.example',CURRENT_TIMESTAMP)"""
            )
            self.school_b = cursor.lastrowid
            connection.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES ('config_admin_a','Config Admin A',?,'school_admin','active',0,1)""",
                (generate_password_hash("config-admin-a-password"),),
            )
            connection.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES ('config_admin_b','Config Admin B',?,'school_admin','active',0,?)""",
                (generate_password_hash("config-admin-b-password"), self.school_b),
            )
            connection.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES ('config_viewer','Config Viewer',?,'viewer','active',0,1)""",
                (generate_password_hash("config-viewer-password"),),
            )
            connection.commit()
        finally:
            connection.close()
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO campuses(school_id,code,name,is_main,city,county,country_code,
                   contact_email,contact_phone)
                   VALUES (1,'MAIN','School A Main Campus',1,'Monrovia','Montserrado','LR',
                   'main@school-a.example','+231000000001')"""
            )
            connection.execute(
                """INSERT INTO campuses(school_id,code,name,is_main,city,country_code)
                   VALUES (?,'MAIN','School B Main Campus',1,'Accra','GH')""", (self.school_b,)
            )
            connection.commit()
            self.campus_a = connection.execute(
                "SELECT id FROM campuses WHERE school_id=1"
            ).fetchone()["id"]
            self.campus_b = connection.execute(
                "SELECT id FROM campuses WHERE school_id=?", (self.school_b,)
            ).fetchone()["id"]
        finally:
            connection.close()
        initialize_configuration_schema(portal.get_db_connection)
        self.client = portal.app.test_client()

    def tearDown(self):
        portal.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def login(self, username, password):
        return self.client.post("/login", data={"username": username, "password": password})

    def test_migration_preserves_legacy_school_settings_and_is_idempotent(self):
        connection = portal.get_db_connection()
        try:
            before = connection.execute(
                "SELECT id,name,contact_email,country_code,timezone FROM schools WHERE id=1"
            ).fetchone()
            row = connection.execute(
                "SELECT version,config_json FROM school_configurations WHERE school_id=1"
            ).fetchone()
            self.assertEqual(before["name"], "CMHS Digital Campus")
            self.assertEqual(row["version"], 1)
            import json
            config = json.loads(row["config_json"])
            self.assertEqual(config["branding"]["display_name"], before["name"])
            self.assertEqual(config["contact"]["email"], before["contact_email"])
            self.assertEqual(config["regional"]["timezone"], "Africa/Monrovia")
            self.assertEqual(config["contact"]["country_code"], "LR")
            history_before = connection.execute(
                "SELECT COUNT(*) AS n FROM school_configuration_versions WHERE school_id=1"
            ).fetchone()["n"]
        finally:
            connection.close()
        initialize_configuration_schema(portal.get_db_connection)
        connection = portal.get_db_connection()
        try:
            history_after = connection.execute(
                "SELECT COUNT(*) AS n FROM school_configuration_versions WHERE school_id=1"
            ).fetchone()["n"]
            self.assertEqual(history_before, history_after)
            self.assertEqual(connection.execute("SELECT id FROM schools WHERE id=1").fetchone()["id"], 1)
            campus_override = connection.execute(
                "SELECT overrides_json FROM campus_configurations WHERE school_id=1 AND campus_id=?",
                (self.campus_a,),
            ).fetchone()
            import json
            campus_config = json.loads(campus_override["overrides_json"])
            self.assertEqual(campus_config["branding"]["display_name"], "School A Main Campus")
            self.assertEqual(campus_config["contact"]["email"], "main@school-a.example")
        finally:
            connection.close()

    def test_configuration_centre_is_available_to_school_admin(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.get("/school-network/configuration")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"School Configuration Centre", response.data)
        self.assertIn(b"Academic calendar", response.data)
        self.assertIn(b"Grading system", response.data)

    def test_tenant_cannot_read_another_schools_configuration(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.get(
            f"/api/v1/schools/{self.school_b}/configuration"
        )
        self.assertEqual(response.status_code, 404)

    def test_configuration_update_is_versioned_and_isolated(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.patch(
            "/api/v1/schools/1/configuration",
            json={
                "expected_version": 1,
                "configuration": {
                    "branding": {"display_name": "School A International"},
                    "regional": {
                        "currency": "GHS", "timezone": "Africa/Accra",
                        "default_language": "en", "supported_languages": ["en", "fr"]
                    },
                    "modules": {"enabled": ["school_management", "learning_management"]}
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["version"], 2)
        other = self.client.get(f"/api/v1/schools/{self.school_b}/configuration")
        self.assertEqual(other.status_code, 404)
        connection = portal.get_db_connection()
        try:
            a = connection.execute(
                "SELECT name FROM schools WHERE id=1"
            ).fetchone()["name"]
            b = connection.execute(
                "SELECT name FROM schools WHERE id=?", (self.school_b,)
            ).fetchone()["name"]
            self.assertEqual(a, "School A International")
            self.assertEqual(b, "School B")
            history = connection.execute(
                "SELECT version FROM school_configuration_versions WHERE school_id=1 AND campus_id IS NULL ORDER BY version"
            ).fetchall()
            self.assertEqual([row["version"] for row in history], [1, 2])
            audit = connection.execute(
                "SELECT action FROM school_configuration_audit WHERE school_id=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()["action"]
            self.assertEqual(audit, "configuration.updated")
        finally:
            connection.close()

    def test_invalid_currency_timezone_and_grading_are_rejected(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.patch(
            "/api/v1/schools/1/configuration",
            json={"configuration": {
                "regional": {"currency": "US", "timezone": "Not/AZone"},
                "grading_system": {"passing_score": 150, "grades": []}
            }},
        )
        self.assertEqual(response.status_code, 422)
        self.assertGreaterEqual(len(response.get_json()["errors"]), 3)

    def test_optimistic_version_conflict_prevents_overwrite(self):
        self.login("config_admin_a", "config-admin-a-password")
        first = self.client.patch(
            "/api/v1/schools/1/configuration",
            json={"configuration": {"branding": {"display_name": "Version Two"}}, "expected_version": 1},
        )
        self.assertEqual(first.status_code, 200)
        stale = self.client.patch(
            "/api/v1/schools/1/configuration",
            json={"configuration": {"branding": {"display_name": "Stale Write"}}, "expected_version": 1},
        )
        self.assertEqual(stale.status_code, 409)

    def test_campus_override_is_scoped_and_inherits_organization_defaults(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.patch(
            f"/api/v1/schools/1/campuses/{self.campus_a}/configuration",
            json={"overrides": {"branding": {"display_name": "School A West Campus"}}},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        effective = self.client.get(
            f"/api/v1/schools/1/campuses/{self.campus_a}/configuration"
        )
        data = effective.get_json()
        self.assertEqual(data["effective_configuration"]["branding"]["display_name"], "School A West Campus")
        self.assertEqual(data["effective_configuration"]["regional"]["timezone"], "Africa/Monrovia")
        cross = self.client.get(
            f"/api/v1/schools/1/campuses/{self.campus_b}/configuration"
        )
        self.assertEqual(cross.status_code, 404)

    def test_campus_cannot_override_organization_module_entitlements(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.patch(
            f"/api/v1/schools/1/campuses/{self.campus_a}/configuration",
            json={"overrides": {"modules": {"enabled": ["education_finance"]}}},
        )
        self.assertEqual(response.status_code, 422)

    def test_configuration_history_is_tenant_scoped(self):
        self.login("config_admin_a", "config-admin-a-password")
        response = self.client.get("/api/v1/schools/1/configuration/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"][0]["version"], 1)
        forbidden = self.client.get(f"/api/v1/schools/{self.school_b}/configuration/history")
        self.assertEqual(forbidden.status_code, 404)

    def test_non_administrator_cannot_access_configuration(self):
        self.login("config_teacher", "config-viewer-password")
        response = self.client.get("/school-network/configuration")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
