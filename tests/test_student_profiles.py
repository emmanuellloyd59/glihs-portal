"""Regression tests for expanded student profiles and edit permissions."""
import unittest
from test_app import PortalTestSupport
import app as portal


class StudentProfileTests(PortalTestSupport, unittest.TestCase):
    def create_student(self, admission="PROFILE-DEMO-01"):
        connection = portal.get_db_connection()
        try:
            cur = connection.execute(
                """INSERT INTO students
                   (admission_number, full_name, class_level, enrollment_date,
                    previous_school, emergency_contact_name, emergency_contact_phone,
                    record_notes, school_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (admission, "Alex Fiction", "Grade 7", "2026-09-01",
                 "Example Primary", "Jamie Fiction", "000-111-2222", "Fictional note"),
            )
            connection.commit()
            return cur.lastrowid
        finally:
            connection.close()

    def test_teacher_student_profile_hides_private_contact_details(self):
        student_id = self.create_student()
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get(f"/students/{student_id}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Example Primary", response.data)
        self.assertNotIn(b"Jamie Fiction", response.data)
        self.assertNotIn(b"000-111-2222", response.data)
        self.assertIn(b"2026-09-01", response.data)
        self.assertIn(b"Financial information is restricted", response.data)
        self.assertNotIn(b"Current Academic-Year Fees", response.data)

    def test_teacher_cannot_edit_student_record(self):
        student_id = self.create_student()
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.get(f"/students/{student_id}/edit")
        self.assertEqual(response.status_code, 403)

    def test_registrar_can_update_class_and_contact_details(self):
        student_id = self.create_student()
        self.login_as("registrar_demo", "test-registrar-password")
        response = self.client.post(f"/students/{student_id}/edit", data={
            "full_name": "Alex Fiction",
            "class_level": "Grade 8",
            "date_of_birth": "",
            "enrollment_date": "2026-09-01",
            "guardian_name": "Pat Fiction",
            "guardian_phone": "000-222-3333",
            "previous_school": "Example Primary",
            "emergency_contact_name": "Jamie Fiction",
            "emergency_contact_phone": "000-111-2222",
            "record_notes": "Class updated in test",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Grade 8", response.data)
        connection = portal.get_db_connection()
        try:
            row = connection.execute(
                "SELECT class_level, guardian_phone FROM students WHERE id = ?",
                (student_id,),
            ).fetchone()
            self.assertEqual(row["class_level"], "Grade 8")
            self.assertEqual(row["guardian_phone"], "000-222-3333")
        finally:
            connection.close()


if __name__ == "__main__":
    import unittest
    unittest.main()
