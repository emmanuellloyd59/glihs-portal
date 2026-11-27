"""Tenant-isolation and privacy tests for Student Information Management."""
import io
import unittest

from test_app import PortalTestSupport
import app as portal
from werkzeug.security import generate_password_hash
from student_information import initialize_student_information_schema


class StudentInformationTests(PortalTestSupport, unittest.TestCase):
    def create_student(self, school_id=1, admission="SIM-001", identifier="STU-2026-00001",
                       name="Alex Fiction", dob="2012-04-03"):
        db = portal.get_db_connection()
        try:
            cur = db.execute("""
              INSERT INTO students(admission_number,student_id_number,full_name,class_level,
                date_of_birth,guardian_name,guardian_phone,school_id)
              VALUES(?,?,?,?,?,?,?,?)
            """, (admission, identifier, name, "Grade 7", dob,
                  "Pat Fiction", "000-111-2222", school_id))
            db.commit()
            return cur.lastrowid
        finally:
            db.close()

    def create_second_school_registrar(self):
        db = portal.get_db_connection()
        try:
            cur = db.execute("""
              INSERT INTO schools(name,slug,status,lifecycle_status)
              VALUES('Second Test School','second-test-school','active','active')
            """)
            school_id = cur.lastrowid
            user = db.execute("""
              INSERT INTO users(username,full_name,password_hash,role,status,school_id)
              VALUES('registrar_two','Second Registrar','test-hash','registrar','active',?)
            """, (school_id,))
            user_id = user.lastrowid
            db.execute("""
              INSERT INTO school_memberships(user_id,school_id,role,status,is_primary)
              VALUES(?,?, 'registrar','active',1)
            """, (user_id, school_id))
            db.commit()
            return school_id
        finally:
            db.close()

    def test_schema_is_idempotent_and_existing_student_is_preserved(self):
        student_id = self.create_student()
        initialize_student_information_schema(portal.get_db_connection)
        initialize_student_information_schema(portal.get_db_connection)
        db = portal.get_db_connection()
        try:
            row = db.execute("SELECT id,admission_number,full_name FROM students WHERE id=?",
                             (student_id,)).fetchone()
            self.assertEqual((row["id"],row["admission_number"],row["full_name"]),
                             (student_id,"SIM-001","Alex Fiction"))
        finally:
            db.close()

    def test_same_identifiers_are_allowed_in_different_schools(self):
        second_school = self.create_second_school_registrar()
        first = self.create_student(1,"SHARED-001","SHARED-ID")
        second = self.create_student(second_school,"SHARED-001","SHARED-ID")
        self.assertNotEqual(first,second)

    def test_same_admission_number_is_rejected_within_one_school(self):
        self.create_student()
        db = portal.get_db_connection()
        try:
            with self.assertRaises(Exception):
                db.execute("""
                  INSERT INTO students(admission_number,full_name,class_level,school_id)
                  VALUES('sim-001','Duplicate Fiction','Grade 7',1)
                """)
        finally:
            db.close()

    def test_student_record_is_not_visible_across_school_tenants(self):
        second_school = self.create_second_school_registrar()
        student_id = self.create_student(second_school,"SCHOOL2-001","SCHOOL2-ID")
        self.login_as("registrar_demo","test-registrar-password")
        response = self.client.get(f"/api/v1/students/{student_id}")
        self.assertEqual(response.status_code,404)

    def test_teacher_api_response_excludes_sensitive_student_fields(self):
        student_id = self.create_student()
        self.login_as("teacher_demo","test-teacher-password")
        response = self.client.get(f"/api/v1/students/{student_id}")
        self.assertEqual(response.status_code,200)
        record = response.get_json()["student"]
        self.assertNotIn("date_of_birth",record)
        self.assertNotIn("guardian_name",record)
        self.assertNotIn("guardian_phone",record)

    def test_teacher_cannot_read_guardians_or_disciplinary_records(self):
        student_id = self.create_student()
        self.login_as("teacher_demo","test-teacher-password")
        self.assertEqual(self.client.get(f"/api/v1/students/{student_id}/guardians").status_code,403)
        self.assertEqual(self.client.get(f"/api/v1/students/{student_id}/disciplinary-records").status_code,403)

    def test_registrar_can_manage_guardians_and_academic_history(self):
        student_id = self.create_student()
        self.login_as("registrar_demo","test-registrar-password")
        guardian = self.client.post(f"/api/v1/students/{student_id}/guardians",json={
            "full_name":"Pat Fiction","relationship":"Parent","phone":"000-333-4444",
            "email":"parent@example.test","is_primary":True,"consent_status":"granted"
        })
        self.assertEqual(guardian.status_code,201)
        history = self.client.post(f"/api/v1/students/{student_id}/academic-history",json={
            "institution_name":"Example Primary","academic_year":"2024-2025","class_level":"Grade 6"
        })
        self.assertEqual(history.status_code,201)
        listed = self.client.get(f"/api/v1/students/{student_id}/guardians")
        self.assertEqual(listed.status_code,200)
        self.assertEqual(len(listed.get_json()["items"]),1)

    def test_admission_duplicate_is_scoped_to_school(self):
        second_school = self.create_second_school_registrar()
        self.login_as("registrar_demo","test-registrar-password")
        payload={"application_number":"APP-001","applicant_name":"Jordan Fiction","requested_class":"Grade 7"}
        self.assertEqual(self.client.post("/api/v1/admissions",json=payload).status_code,201)
        self.assertEqual(self.client.post("/api/v1/admissions",json=payload).status_code,409)
        self.client.post("/logout")
        self.login_as("registrar_two","test-registrar-password")
        self.assertEqual(self.client.post("/api/v1/admissions",json=payload).status_code,201)

    def test_admission_review_and_conversion_allocate_school_identifier(self):
        self.login_as("registrar_demo","test-registrar-password")
        created=self.client.post("/api/v1/admissions",json={
            "application_number":"APP-CONVERT-1","applicant_name":"Taylor Fiction",
            "requested_class":"Grade 8","status":"submitted"
        })
        admission_id=created.get_json()["admission_id"]
        self.assertEqual(self.client.post(f"/api/v1/admissions/{admission_id}/review",
            json={"status":"accepted"}).status_code,200)
        converted=self.client.post(f"/api/v1/admissions/{admission_id}/convert",
            json={"admission_number":"NEW-001","class_level":"Grade 8"})
        self.assertEqual(converted.status_code,201)
        self.assertTrue(converted.get_json()["student_identifier"].startswith("STU-"))

    def test_withdrawal_is_audited_and_preserves_student_row(self):
        student_id=self.create_student()
        self.login_as("registrar_demo","test-registrar-password")
        response=self.client.post(f"/api/v1/students/{student_id}/withdraw",json={
            "effective_date":"2026-11-27","reason_code":"family_relocation"
        })
        self.assertEqual(response.status_code,200)
        db=portal.get_db_connection()
        try:
            row=db.execute("SELECT status FROM students WHERE id=? AND school_id=1",(student_id,)).fetchone()
            history=db.execute("SELECT COUNT(*) FROM student_withdrawals WHERE student_id=? AND school_id=1",
                               (student_id,)).fetchone()[0]
            self.assertEqual(row["status"],"withdrawn")
            self.assertEqual(history,1)
        finally: db.close()

    def test_csv_import_and_export_are_school_scoped(self):
        second_school=self.create_second_school_registrar()
        self.create_student(second_school,"OTHER-001","OTHER-ID","Other Fiction")
        self.login_as("registrar_demo","test-registrar-password")
        data={"file":(io.BytesIO(b"admission_number,full_name,class_level\\nIMP-001,Imported Fiction,Grade 6\\n"),"students.csv")}
        imported=self.client.post("/api/v1/students/import.csv",data=data,content_type="multipart/form-data")
        self.assertEqual(imported.status_code,201)
        self.assertEqual(imported.get_json()["inserted"],1)
        exported=self.client.get("/api/v1/students/export.csv")
        self.assertEqual(exported.status_code,200)
        self.assertIn(b"Imported Fiction",exported.data)
        self.assertNotIn(b"Other Fiction",exported.data)

    def test_identifier_configuration_is_school_specific(self):
        self.login_as("registrar_demo","test-registrar-password")
        response=self.client.put("/api/v1/students/identifier-config",
            json={"pattern":"GLI-{year}-{sequence:04d}"})
        self.assertEqual(response.status_code,200)
        config=self.client.get("/api/v1/students/identifier-config").get_json()
        self.assertEqual(config["pattern"],"GLI-{year}-{sequence:04d}")


if __name__ == "__main__":
    unittest.main()
