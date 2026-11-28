"""Tests for the unified school ERP configuration and safe reporting."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager, UserMixin

from school_erp import (
    CORE_FEATURES,
    OPTIONAL_FEATURES,
    _csv_cell,
    initialize_school_erp_schema,
    create_school_erp_blueprint,
)


class _TestUser(UserMixin):
    id = 10
    role = "school_admin"
    is_super_admin = False


class SchoolERPTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)

        def connect():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db

        self.connect = connect
        db = connect()
        db.execute("CREATE TABLE schools(id INTEGER PRIMARY KEY,name TEXT NOT NULL,status TEXT DEFAULT 'active')")
        db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO schools(id,name) VALUES(?,?)", [(1,"School A"),(2,"School B")])
        db.commit()
        db.close()
        initialize_school_erp_schema(self.connect)

    def tearDown(self):
        os.unlink(self.path)

    def test_schema_initialization_is_idempotent(self):
        initialize_school_erp_schema(self.connect)
        db = self.connect()
        try:
            count = db.execute("SELECT COUNT(*) FROM school_feature_entitlements").fetchone()[0]
            self.assertEqual(count, 2 * (len(CORE_FEATURES) + len(OPTIONAL_FEATURES)))
        finally:
            db.close()

    def test_core_features_are_enabled_for_each_school(self):
        db = self.connect()
        try:
            rows = db.execute(
                "SELECT school_id,feature_key,enabled FROM school_feature_entitlements"
            ).fetchall()
            for row in rows:
                if row["feature_key"] in CORE_FEATURES:
                    self.assertEqual(row["enabled"], 1)
                else:
                    self.assertEqual(row["enabled"], 0)
        finally:
            db.close()

    def test_entitlements_are_school_scoped(self):
        db = self.connect()
        try:
            db.execute(
                "UPDATE school_feature_entitlements SET enabled=1 WHERE school_id=1 AND feature_key='library'"
            )
            db.commit()
            a = db.execute(
                "SELECT enabled FROM school_feature_entitlements WHERE school_id=1 AND feature_key='library'"
            ).fetchone()[0]
            b = db.execute(
                "SELECT enabled FROM school_feature_entitlements WHERE school_id=2 AND feature_key='library'"
            ).fetchone()[0]
            self.assertEqual((a,b),(1,0))
        finally:
            db.close()

    def test_csv_formula_prefixes_are_neutralized(self):
        for value in ("=1+1", "+SUM(A1:A2)", "-10", "@cmd", "\tvalue"):
            self.assertTrue(_csv_cell(value).startswith("'"))
        self.assertEqual(_csv_cell("ordinary text"), "ordinary text")

    def test_empty_values_are_safe(self):
        self.assertEqual(_csv_cell(None), "")

    def _client_with_school_data(self):
        db = self.connect()
        db.executescript("""
          CREATE TABLE students(
            id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL, admission_number TEXT,
            student_id_number TEXT, full_name TEXT, class_level TEXT, status TEXT,
            enrollment_date TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
          );
          CREATE TABLE student_admissions(
            id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL, application_number TEXT,
            applicant_name TEXT, requested_class TEXT, status TEXT, submitted_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
          );
          CREATE TABLE staff_memberships(
            id INTEGER PRIMARY KEY, school_id INTEGER, membership_status TEXT
          );
          CREATE TABLE grade_levels(
            id INTEGER PRIMARY KEY, school_id INTEGER, active INTEGER
          );
          CREATE TABLE teacher_class_assignments(
            id INTEGER PRIMARY KEY, school_id INTEGER, teacher_id INTEGER,
            class_level TEXT, subject TEXT, active INTEGER
          );
          CREATE TABLE attendance(
            id INTEGER PRIMARY KEY, school_id INTEGER, student_id INTEGER,
            attendance_date TEXT, status TEXT, note TEXT
          );
          CREATE TABLE grades(
            id INTEGER PRIMARY KEY, school_id INTEGER, student_id INTEGER,
            subject TEXT, assessment TEXT, score REAL, max_score REAL,
            academic_year TEXT, term TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
          );
          CREATE TABLE configurable_assessments(id INTEGER PRIMARY KEY,school_id INTEGER);
          CREATE TABLE report_cards(id INTEGER PRIMARY KEY,school_id INTEGER);
          CREATE TABLE student_disciplinary_records(id INTEGER PRIMARY KEY,school_id INTEGER);
          CREATE TABLE student_finances(
            id INTEGER PRIMARY KEY,school_id INTEGER,academic_year TEXT,
            total_fee REAL,amount_paid REAL
          );
        """)
        db.execute("INSERT INTO students VALUES(1,1,'A-001','STU-1','Student One','Grade 7','active','2026-09-01',CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO students VALUES(2,2,'B-001','STU-2','Other School Student','Grade 7','active','2026-09-01',CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO student_admissions(school_id,application_number,applicant_name,requested_class,status) VALUES(1,'APP-1','Student One','Grade 7','converted')")
        db.execute("INSERT INTO staff_memberships(school_id,membership_status) VALUES(1,'active')")
        db.execute("INSERT INTO grade_levels(school_id,active) VALUES(1,1)")
        db.execute("INSERT INTO teacher_class_assignments(school_id,teacher_id,class_level,subject,active) VALUES(1,99,'Grade 7','Mathematics',1)")
        db.execute("INSERT INTO attendance(school_id,student_id,attendance_date,status) VALUES(1,1,'2026-09-02','present')")
        db.execute("INSERT INTO grades(school_id,student_id,subject,assessment,score,max_score,academic_year,term) VALUES(1,1,'Mathematics','Test 1',18,20,'2026-2027','Term 1')")
        db.execute("INSERT INTO configurable_assessments(school_id) VALUES(1)")
        db.execute("INSERT INTO report_cards(school_id) VALUES(1)")
        db.execute("INSERT INTO student_disciplinary_records(school_id) VALUES(1)")
        db.commit()
        db.close()

        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["TESTING"] = True
        login_manager = LoginManager(app)
        login_manager.user_loader(lambda user_id: _TestUser() if user_id == "10" else None)
        app.register_blueprint(create_school_erp_blueprint(self.connect, lambda: 1))
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "10"
            session["_fresh"] = True
        return client

    def test_enrollment_class_attendance_grading_report_pipeline_is_school_scoped(self):
        with patch("authorization.require_permission", lambda *args, **kwargs: None), \
             patch("authorization.has_permission", lambda *args, **kwargs: True):
            client = self._client_with_school_data()
            students = client.get("/api/erp/reports/students")
            self.assertEqual(students.status_code, 200)
            self.assertEqual(students.json["count"], 1)
            self.assertEqual(students.json["rows"][0]["full_name"], "Student One")

            admissions = client.get("/api/erp/reports/admissions")
            self.assertEqual(admissions.status_code, 200)
            self.assertEqual(admissions.json["count"], 1)

            attendance = client.get("/api/erp/reports/attendance?date_from=2026-09-01&date_to=2026-09-30")
            self.assertEqual(attendance.status_code, 200)
            self.assertEqual(attendance.json["count"], 1)

            grades = client.get("/api/erp/reports/grades?academic_year=2026-2027&term=Term%201")
            self.assertEqual(grades.status_code, 200)
            self.assertEqual(grades.json["count"], 1)
            self.assertEqual(grades.json["rows"][0]["subject"], "Mathematics")

            dashboard = client.get("/api/erp/dashboard")
            self.assertEqual(dashboard.status_code, 200)
            self.assertEqual(dashboard.json["school"]["id"], 1)
            self.assertEqual(dashboard.json["metrics"]["teacher_class_assignments"], 1)
            self.assertEqual(dashboard.json["metrics"]["assessments"], 1)
            self.assertEqual(dashboard.json["metrics"]["report_cards"], 1)
            self.assertEqual(dashboard.json["metrics"]["discipline_records"], 1)

    def test_csv_export_is_available_for_authorized_students(self):
        with patch("authorization.require_permission", lambda *args, **kwargs: None), \
             patch("authorization.has_permission", lambda *args, **kwargs: True):
            client = self._client_with_school_data()
            response = client.get("/api/erp/reports/students?format=csv")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Student One", response.data)
            self.assertNotIn(b"Other School Student", response.data)


if __name__ == "__main__":
    unittest.main()
