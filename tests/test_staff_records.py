"""Tenant-scoped staff lifecycle integration tests.

Run with: python -m unittest tests.test_staff_records -v
All records and uploaded documents are fictional and isolated to a temporary database.
"""
import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("SECRET_KEY", "staff-record-tests-only-secret")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP.name) / "import.db"))
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

from werkzeug.security import generate_password_hash  # noqa: E402
import app as portal  # noqa: E402
from staff_records import initialize_staff_records  # noqa: E402


class StaffRecordLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        portal.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)

    def setUp(self):
        self.database = Path(_TMP.name) / (self.id().replace(".", "_") + ".db")
        portal.DATABASE = self.database
        portal.initialize_database()
        initialize_staff_records(portal.get_db_connection)
        self.client = portal.app.test_client()
        db = portal.get_db_connection()
        try:
            # Lifecycle events are append-only; each test uses a fresh database.
            db.execute("DELETE FROM staff_documents")
            db.execute("DELETE FROM staff_responsibilities")
            db.execute("DELETE FROM staff_qualifications")
            db.execute("DELETE FROM staff_employment")
            db.execute("DELETE FROM staff_profiles")
            db.execute("DELETE FROM staff_memberships")
            db.execute("DELETE FROM staff_departments")
            db.execute("DELETE FROM teacher_class_assignments")
            db.execute("DELETE FROM audit_log")
            db.execute("DELETE FROM users")
            db.execute("DELETE FROM schools WHERE id<>1")
            db.execute("UPDATE schools SET status='active' WHERE id=1")
            db.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES('staff_admin','Staff Administrator',?,'school_admin','active',0,1)""",
                (generate_password_hash("staff-admin-password"),),
            )
            db.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES('existing_teacher','Existing Teacher',?,'teacher','active',0,1)""",
                (generate_password_hash("existing-teacher-password"),),
            )
            db.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES('inactive_teacher','Inactive Teacher',?,'teacher','suspended',0,1)""",
                (generate_password_hash("inactive-teacher-password"),),
            )
            db.commit()
        finally:
            db.close()
        initialize_staff_records(portal.get_db_connection)
        self.admin_id = self.user_id("staff_admin")
        self.teacher_id = self.user_id("existing_teacher")
        self.inactive_id = self.user_id("inactive_teacher")

    def tearDown(self):
        if self.client:
            self.client.get("/logout")
        portal.DATABASE = Path(os.environ["DATABASE_PATH"])

    def user_id(self, username):
        db = portal.get_db_connection()
        try:
            return db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
        finally:
            db.close()

    def login(self):
        response = self.client.post("/login", data={
            "username": "staff_admin", "password": "staff-admin-password"
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def membership(self, user_id, school_id=1):
        db = portal.get_db_connection()
        try:
            return db.execute(
                """SELECT m.id,m.membership_status,e.employment_status
                   FROM staff_memberships m JOIN staff_employment e
                     ON e.school_id=m.school_id AND e.membership_id=m.id
                   WHERE m.school_id=? AND m.user_id=?""",
                (school_id,user_id),
            ).fetchone()
        finally:
            db.close()

    def test_existing_teacher_accounts_are_linked_without_password_or_identity_reset(self):
        db = portal.get_db_connection()
        try:
            before = db.execute("SELECT id,password_hash,role FROM users WHERE id=?", (self.teacher_id,)).fetchone()
            membership = db.execute(
                "SELECT id,membership_status FROM staff_memberships WHERE school_id=1 AND user_id=?",
                (self.teacher_id,),
            ).fetchone()
            after = db.execute("SELECT id,password_hash,role FROM users WHERE id=?", (self.teacher_id,)).fetchone()
        finally:
            db.close()
        self.assertEqual((before["id"],before["password_hash"],before["role"]),
                         (after["id"],after["password_hash"],after["role"]))
        self.assertIsNotNone(membership)
        self.assertEqual(membership["membership_status"],"active")
        self.assertEqual(self.membership(self.inactive_id)["employment_status"],"suspended")

    def test_school_admin_can_create_staff_account_and_membership_atomically(self):
        self.login()
        response = self.client.post("/staff", data={
            "action":"create","username":"new_teacher","full_name":"New Teacher",
            "role":"teacher","temporary_password":"temporary-strong-password"
        }, follow_redirects=True)
        self.assertEqual(response.status_code,200)
        new_id=self.user_id("new_teacher")
        self.assertEqual(self.membership(new_id)["membership_status"],"active")
        db=portal.get_db_connection()
        try:
            event=db.execute(
                """SELECT event_type FROM staff_lifecycle_events
                   WHERE school_id=1 AND membership_id=(SELECT id FROM staff_memberships WHERE user_id=?)""",
                (new_id,),
            ).fetchone()
            self.assertEqual(event["event_type"],"onboarded")
        finally:
            db.close()

    def test_profile_employment_qualification_and_department_updates(self):
        self.login()
        detail=f"/staff-records/{self.teacher_id}"
        response=self.client.post(detail,data={
            "action":"update_profile","employee_number":"EMP-1001","preferred_name":"Teacher T",
            "phone":"+231 770 000 000","email":"teacher@example.org","address":"Monrovia",
            "emergency_contact_name":"Contact Person","emergency_contact_phone":"+231 880 000 000",
            "date_of_birth":"1990-01-01","gender":"prefer_not_to_say","profile_notes":"Fictional test profile"
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b"Teacher T",response.data)
        response=self.client.post(detail,data={
            "action":"create_department","department_name":"Science Department",
            "department_description":"Science subjects"
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        response=self.client.post(detail,data={
            "action":"update_employment","job_title":"Science Teacher",
            "department_id":"1","supervisor_membership_id":"","employment_type":"full_time",
            "employment_status":"active","start_date":"2024-09-01","end_date":"",
            "work_location":"Main Campus","responsibilities_summary":"Teach science"
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        response=self.client.post(detail,data={
            "action":"add_qualification","qualification":"Bachelor of Education",
            "institution":"National University","field_of_study":"Science Education",
            "award_date":"2018-06-01","notes":"Verified documents pending"
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b"Bachelor of Education",response.data)
        db=portal.get_db_connection()
        try:
            row=db.execute(
                """SELECT p.employee_number,e.job_title,e.department_id
                   FROM staff_profiles p JOIN staff_employment e
                     ON e.school_id=p.school_id AND e.membership_id=p.membership_id
                   WHERE p.school_id=1 AND p.membership_id=(SELECT id FROM staff_memberships WHERE user_id=?)""",
                (self.teacher_id,),
            ).fetchone()
            self.assertEqual((row["employee_number"],row["job_title"],row["department_id"]),
                             ("EMP-1001","Science Teacher",1))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM staff_qualifications WHERE school_id=1").fetchone()[0],1)
        finally:
            db.close()

    def test_onboarding_and_offboarding_revoke_and_restore_access(self):
        self.login()
        db=portal.get_db_connection()
        try:
            db.execute(
                """INSERT INTO teacher_class_assignments(school_id,teacher_id,class_level,subject,active)
                   VALUES(1,?,'Grade 7','Mathematics',1)""",(self.inactive_id,)
            )
            db.commit()
        finally:
            db.close()
        detail=f"/staff-records/{self.inactive_id}"
        response=self.client.post(detail,data={"action":"onboard"},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.membership(self.inactive_id)["membership_status"],"active")
        response=self.client.post(detail,data={"action":"offboard","notes":"End of contract"},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        state=self.membership(self.inactive_id)
        self.assertEqual((state["membership_status"],state["employment_status"]),("inactive","terminated"))
        db=portal.get_db_connection()
        try:
            user=db.execute("SELECT status FROM users WHERE id=?",(self.inactive_id,)).fetchone()
            assignment=db.execute("SELECT active FROM teacher_class_assignments WHERE teacher_id=?",(self.inactive_id,)).fetchone()
            events=db.execute("SELECT event_type FROM staff_lifecycle_events WHERE school_id=1 AND membership_id=(SELECT id FROM staff_memberships WHERE user_id=?) ORDER BY id",(self.inactive_id,)).fetchall()
            self.assertEqual(user["status"],"suspended")
            self.assertEqual(assignment["active"],0)
            self.assertEqual([r["event_type"] for r in events],["onboarded","offboarded"])
            event_id=db.execute(
                "SELECT id FROM staff_lifecycle_events WHERE membership_id=(SELECT id FROM staff_memberships WHERE user_id=?) ORDER BY id DESC LIMIT 1",
                (self.inactive_id,),
            ).fetchone()["id"]
            with self.assertRaises(Exception):
                db.execute("UPDATE staff_lifecycle_events SET notes='changed' WHERE id=?",(event_id,))
            db.rollback()
            with self.assertRaises(Exception):
                db.execute("DELETE FROM staff_lifecycle_events WHERE id=?",(event_id,))
            db.rollback()
        finally:
            db.close()

    def test_staff_documents_are_private_tenant_scoped_and_archivable(self):
        self.login()
        detail=f"/staff-records/{self.teacher_id}"
        payload=b"%PDF-1.4\nfictional employee record\n%%EOF"
        response=self.client.post(detail,data={
            "action":"upload_document","document_type":"Qualification Certificate",
            "document":( __import__("io").BytesIO(payload),"certificate.pdf")
        },content_type="multipart/form-data",follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b"certificate.pdf",response.data)
        db=portal.get_db_connection()
        try:
            doc=db.execute("SELECT id,storage_key,sha256 FROM staff_documents WHERE school_id=1").fetchone()
            self.assertEqual(len(doc["sha256"]),64)
        finally:
            db.close()
        download=self.client.get(f"/staff-records/{self.teacher_id}/documents/{doc['id']}")
        self.assertEqual(download.status_code,200)
        self.assertEqual(download.data,payload)
        response=self.client.post(detail,data={"action":"archive_document","item_id":str(doc["id"])},follow_redirects=True)
        self.assertEqual(response.status_code,200)
        denied=self.client.get(f"/staff-records/{self.teacher_id}/documents/{doc['id']}")
        self.assertEqual(denied.status_code,404)

    def test_cross_school_staff_records_are_not_discoverable(self):
        db=portal.get_db_connection()
        try:
            school_id=db.execute(
                "INSERT INTO schools(name,slug,status) VALUES('Other School','other-school','active')"
            ).lastrowid
            other_id=db.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES('other_teacher','Other Teacher',?,'teacher','active',0,?)""",
                (generate_password_hash("other-teacher-password"),school_id),
            ).lastrowid
            db.commit()
        finally:
            db.close()
        initialize_staff_records(portal.get_db_connection)
        self.login()
        response=self.client.get(f"/staff-records/{other_id}")
        self.assertEqual(response.status_code,404)

    def test_non_admin_cannot_view_staff_records_and_admin_cannot_offboard_self(self):
        self.login()
        self_id=self.admin_id
        denied=self.client.post(f"/staff-records/{self_id}",data={"action":"offboard","notes":"self"},follow_redirects=True)
        self.assertEqual(denied.status_code,403)
        self.client.post("/logout")
        self.client.post("/login",data={"username":"existing_teacher","password":"existing-teacher-password"})
        denied=self.client.get(f"/staff-records/{self.teacher_id}")
        self.assertEqual(denied.status_code,403)

    def test_tenant_triggers_reject_cross_school_staff_membership_and_subjects(self):
        db=portal.get_db_connection()
        try:
            school_id=db.execute(
                "INSERT INTO schools(name,slug,status) VALUES('Other School','other-school-trigger','active')"
            ).lastrowid
            with self.assertRaises(Exception):
                db.execute("INSERT INTO staff_memberships(school_id,user_id) VALUES(?,?)",(school_id,self.teacher_id))
            db.rollback()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
