"""Public website routing, publication and tenant isolation tests."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "school-websites-tests-only-secret")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import app as portal
from school_network import initialize_school_network_schema
from identity_memberships import initialize_membership_schema
from authorization import initialize_authorization_schema
from school_websites import initialize_school_website_schema


class SchoolWebsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = portal.DATABASE
        portal.DATABASE = Path(self.temp_dir.name) / "websites.sqlite3"
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        portal.initialize_database()
        initialize_school_network_schema(portal.get_db_connection)
        initialize_membership_schema(portal.get_db_connection)
        initialize_authorization_schema(portal.get_db_connection)
        initialize_school_website_schema(portal.get_db_connection)
        connection = portal.get_db_connection()
        try:
            connection.execute(
                "UPDATE schools SET status='active',lifecycle_status='active' WHERE id=1"
            )
            cursor = connection.execute(
                "INSERT INTO schools(name,slug,status) VALUES('School B','school-b','active')"
            )
            self.school_b = cursor.lastrowid
            connection.execute(
                "UPDATE schools SET lifecycle_status='active' WHERE id=?", (self.school_b,)
            )
            connection.execute(
                """INSERT INTO school_websites
                   (school_id,name,slug,status,theme_json)
                   VALUES(1,'School A Public Site','school-a-site','published',
                          '{"primary_color":"#174A7E","secondary_color":"#E8A838"}')"""
            )
            self.site_a = connection.execute(
                "SELECT id FROM school_websites WHERE slug='school-a-site'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,content,status,is_home)
                   VALUES(?,1,'home','School A','Welcome to School A','published',1)""",
                (self.site_a,),
            )
            connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,content,status)
                   VALUES(?,1,'about','About School A','Public School A information','published')""",
                (self.site_a,),
            )
            connection.execute(
                """INSERT INTO school_websites
                   (school_id,name,slug,status,theme_json)
                   VALUES(?,'School B Public Site','school-b-site','published','{}')""",
                (self.school_b,),
            )
            self.site_b = connection.execute(
                "SELECT id FROM school_websites WHERE slug='school-b-site'"
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,content,status,is_home)
                   VALUES(?,?, 'home','School B','Welcome to School B','published',1)""",
                (self.site_b, self.school_b),
            )
            connection.execute(
                """INSERT INTO school_websites
                   (school_id,name,slug,status,theme_json)
                   VALUES(1,'Unpublished Site','unpublished-site','draft','{}')"""
            )
            connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,content,status,is_home)
                   VALUES((SELECT id FROM school_websites WHERE slug='unpublished-site'),
                          1,'home','Private Draft','Do not show','published',1)"""
            )
            connection.execute(
                "CREATE TABLE private_erp_fixture(secret TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO private_erp_fixture(secret) VALUES('PRIVATE_FINANCE_SENTINEL')"
            )
            connection.commit()
        finally:
            connection.close()
        self.client = portal.app.test_client()

    def tearDown(self):
        portal.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def test_published_home_and_page_are_public(self):
        home = self.client.get("/school-sites/school-a-site")
        about = self.client.get("/school-sites/school-a-site/about")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"Welcome to School A", home.data)
        self.assertEqual(about.status_code, 200)
        self.assertIn(b"Public School A information", about.data)

    def test_other_school_content_is_not_resolved_under_first_school_slug(self):
        response = self.client.get("/school-sites/school-a-site/home")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Welcome to School B", response.data)
        foreign = self.client.get("/school-sites/school-a-site/secret-page-from-school-b")
        self.assertEqual(foreign.status_code, 404)

    def test_unpublished_website_is_not_public(self):
        response = self.client.get("/school-sites/unpublished-site")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"Do not show", response.data)

    def test_draft_page_is_not_public(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,content,status)
                   VALUES(?,1,'draft-only','Draft Only','DRAFT_SECRET','draft')""",
                (self.site_a,),
            )
            connection.commit()
        finally:
            connection.close()
        response = self.client.get("/school-sites/school-a-site/draft-only")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"DRAFT_SECRET", response.data)

    def test_public_page_does_not_query_or_expose_private_erp_data(self):
        response = self.client.get("/school-sites/school-a-site")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"PRIVATE_FINANCE_SENTINEL", response.data)
        self.assertNotIn(b"student_finances", response.data)
        self.assertNotIn(b"students", response.data)

    def test_public_website_slugs_are_globally_unique(self):
        connection = portal.get_db_connection()
        try:
            with self.assertRaises(Exception):
                connection.execute(
                    """INSERT INTO school_websites(school_id,name,slug,status)
                       VALUES(?,'Duplicate public slug','school-a-site','draft')""",
                    (self.school_b,),
                )
        finally:
            connection.close()

    def test_verified_custom_domain_resolves_only_its_published_website(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO school_website_domains
                   (website_id,school_id,hostname,verification_token,status,verified_at)
                   VALUES(?,1,'www.school-a.example','verification-token','verified',CURRENT_TIMESTAMP)""",
                (self.site_a,),
            )
            connection.commit()
        finally:
            connection.close()
        response = self.client.get("/about", base_url="https://www.school-a.example")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Public School A information", response.data)
        missing = self.client.get("/not-a-published-page", base_url="https://www.school-a.example")
        self.assertEqual(missing.status_code, 404)

    def test_pending_custom_domain_is_not_routed(self):
        connection = portal.get_db_connection()
        try:
            connection.execute(
                """INSERT INTO school_website_domains
                   (website_id,school_id,hostname,verification_token,status)
                   VALUES(?,1,'pending.school-a.example','verification-token','pending')""",
                (self.site_a,),
            )
            connection.commit()
        finally:
            connection.close()
        response = self.client.get("/about", base_url="https://pending.school-a.example")
        self.assertEqual(response.status_code, 404)

    def test_schema_initialization_is_idempotent(self):
        initialize_school_website_schema(portal.get_db_connection)
        connection = portal.get_db_connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS n FROM school_websites").fetchone()["n"],
                3,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS n FROM school_website_pages").fetchone()["n"],
                4,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
