"""Tests for class-based student IDs and financial record access."""
import unittest

from test_app import CMHSPortalTests


class StudentFinanceTests(CMHSPortalTests):
    def test_finance_page_is_restricted_to_authorized_staff(self):
        self.login_as("student_demo", "test-student-password")
        response = self.client.get("/student-finances")
        self.assertEqual(response.status_code, 403)

    def test_student_registration_generates_unique_student_id(self):
        self.login_as("teacher_demo", "test-teacher-password")
        response = self.client.post("/students/register", data={
            "admission_number": "ID-DEMO-001",
            "full_name": "Taylor Example",
            "class_level": "Grade 8",
            "date_of_birth": "",
            "guardian_name": "",
            "guardian_phone": "",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CMHS-", response.data)
        connection = __import__("app").get_db_connection()
        try:
            row = connection.execute(
                "SELECT student_id_number FROM students WHERE admission_number = ?",
                ("ID-DEMO-001",),
            ).fetchone()
            self.assertTrue(row["student_id_number"].startswith("CMHS-"))
        finally:
            connection.close()

    def test_finance_record_calculates_balance_and_rejects_overpayment(self):
        portal = __import__("app")
        connection = portal.get_db_connection()
        try:
            student_id = connection.execute(
                "INSERT INTO students (admission_number, full_name, class_level) VALUES (?, ?, ?)",
                ("FEE-DEMO-001", "Morgan Example", "Grade 9"),
            ).lastrowid
            connection.commit()
        finally:
            connection.close()
        self.login_as("super_demo", "test-super-password")
        response = self.client.post("/student-finances", data={
            "student_id": str(student_id),
            "academic_year": "2026-2027",
            "total_fee": "1000",
            "amount_paid": "650",
            "notes": "fictional test",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"350.00", response.data)
        response = self.client.post("/student-finances", data={
            "student_id": str(student_id),
            "academic_year": "2026-2027",
            "total_fee": "1000",
            "amount_paid": "1200",
            "notes": "",
        }, follow_redirects=True)
        self.assertIn(b"Payments cannot exceed the total fees", response.data)


if __name__ == "__main__":
    unittest.main()
