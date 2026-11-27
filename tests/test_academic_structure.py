"""Tests for tenant-aware configurable academic structures."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from academic_structure import initialize_academic_structure, _snapshot, PERIOD_TYPES


class AcademicStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "academic.db"
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.executescript("""
          PRAGMA foreign_keys=ON;
          CREATE TABLE schools(id INTEGER PRIMARY KEY, status TEXT);
          CREATE TABLE users(id INTEGER PRIMARY KEY);
          CREATE TABLE students(id INTEGER PRIMARY KEY, school_id INTEGER, status TEXT);
          CREATE TABLE grades(id INTEGER PRIMARY KEY, school_id INTEGER, academic_year TEXT, term TEXT, subject TEXT);
          CREATE TABLE report_cards(id INTEGER PRIMARY KEY, school_id INTEGER, academic_year TEXT, term TEXT);
          CREATE TABLE teacher_class_assignments(id INTEGER PRIMARY KEY, school_id INTEGER, class_level TEXT, subject TEXT);
          CREATE TABLE class_timetable(id INTEGER PRIMARY KEY, school_id INTEGER, class_level TEXT, subject TEXT);
          INSERT INTO schools VALUES(1,'active'),(2,'active');
          INSERT INTO students VALUES(10,1,'active'),(20,2,'active');
          INSERT INTO grades VALUES(100,1,'2024-2025','Term 2','Mathematics');
          INSERT INTO report_cards VALUES(200,1,'2024-2025','Term 2');
          INSERT INTO teacher_class_assignments VALUES(1,1,'Grade 6','Mathematics');
          INSERT INTO class_timetable VALUES(1,1,'Grade 6','Science');
        """)
        db.commit(); db.close()
        def opener():
            c=sqlite3.connect(self.path); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
        self.open=opener
        initialize_academic_structure(self.open)

    def tearDown(self):
        self.temp.cleanup()

    def test_imports_legacy_labels_without_imposing_a_national_model(self):
        db=self.open()
        try:
            self.assertEqual(db.execute("SELECT name FROM grade_levels WHERE school_id=1").fetchone()[0],"Grade 6")
            self.assertEqual(db.execute("SELECT name FROM subjects WHERE school_id=1 ORDER BY name").fetchall()[0][0],"Mathematics")
            self.assertIsNotNone(db.execute("SELECT subject_id FROM grades WHERE id=100").fetchone()[0])
            self.assertEqual(tuple(db.execute("SELECT name,period_type FROM academic_periods WHERE school_id=1").fetchone()),("Term 2","custom"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM academic_years WHERE school_id=2").fetchone()[0],0)
        finally: db.close()

    def test_supported_period_types_are_extensible_not_limited_to_terms(self):
        self.assertTrue({"semester","quarter","trimester","block","custom"}.issubset(PERIOD_TYPES))

    def test_tenant_unique_names_do_not_conflict_across_schools(self):
        db=self.open()
        try:
            db.execute("INSERT INTO subjects(school_id,name) VALUES(1,'Art')")
            db.execute("INSERT INTO subjects(school_id,name) VALUES(2,'Art')")
            db.commit()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM subjects WHERE name='Art'").fetchone()[0],2)
        finally: db.close()

    def test_snapshots_are_versioned_and_immutable_records(self):
        db=self.open()
        try:
            db.execute("INSERT INTO academic_years(school_id,label) VALUES(1,'2025/26')")
            _snapshot(db,1,1,"create year"); db.commit()
            self.assertEqual(db.execute("SELECT version_no FROM academic_structure_versions WHERE school_id=1 ORDER BY version_no DESC").fetchone()[0],2)
            payload=db.execute("SELECT snapshot_json FROM academic_structure_versions WHERE school_id=1").fetchone()[0]
            self.assertIn("2025/26",payload)
        finally: db.close()

    def test_database_triggers_reject_cross_tenant_academic_relationships(self):
        db=self.open()
        try:
            db.execute("INSERT INTO academic_years(school_id,label) VALUES(1,'2025/26')")
            year_id=db.execute("SELECT id FROM academic_years WHERE school_id=1 AND label='2025/26'").fetchone()[0]
            db.execute("INSERT INTO grade_levels(school_id,name) VALUES(1,'Level A')")
            level_a=db.execute("SELECT id FROM grade_levels WHERE school_id=1 AND name='Level A'").fetchone()[0]
            db.execute("INSERT INTO grade_levels(school_id,name) VALUES(2,'Level B')")
            level_b=db.execute("SELECT id FROM grade_levels WHERE school_id=2 AND name='Level B'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO academic_classes(school_id,academic_year_id,grade_level_id,name) VALUES(1,?,?, 'Invalid cross-tenant class')",(year_id,level_b))
            db.execute("INSERT INTO academic_classes(school_id,academic_year_id,grade_level_id,name) VALUES(1,?,?, 'Valid class')",(year_id,level_a))
            class_id=db.execute("SELECT id FROM academic_classes WHERE school_id=1 AND name='Valid class'").fetchone()[0]
            db.execute("INSERT INTO class_sections(school_id,class_id,name) VALUES(1,?,'A')",(class_id,))
            section_id=db.execute("SELECT id FROM class_sections WHERE school_id=1 AND class_id=?",(class_id,)).fetchone()[0]
            db.execute("INSERT INTO subjects(school_id,name) VALUES(1,'Mathematics')")
            subject_id=db.execute("SELECT id FROM subjects WHERE school_id=1 AND name='Mathematics'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO course_enrollments(school_id,student_id,class_section_id,subject_id,academic_year_id) VALUES(1,20,?,?,?)",(section_id,subject_id,year_id))
            db.execute("INSERT INTO course_enrollments(school_id,student_id,class_section_id,subject_id,academic_year_id) VALUES(1,10,?,?,?)",(section_id,subject_id,year_id))
            db.commit()
        finally: db.close()

    def test_structure_history_cannot_be_updated_or_deleted(self):
        db=self.open()
        try:
            version_id=db.execute("SELECT id FROM academic_structure_versions WHERE school_id=1").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE academic_structure_versions SET change_note='tampered' WHERE id=?",(version_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM academic_structure_versions WHERE id=?",(version_id,))
        finally: db.close()

    def test_reinitialization_is_idempotent_and_preserves_legacy_grade(self):
        initialize_academic_structure(self.open)
        db=self.open()
        try:
            row=db.execute("SELECT academic_year,term,subject,academic_year_id,academic_period_id FROM grades WHERE id=100").fetchone()
            self.assertEqual(tuple(row[:3]),("2024-2025","Term 2","Mathematics"))
            self.assertIsNotNone(row["subject_id"])
            self.assertIsNotNone(row["academic_year_id"])
            self.assertIsNotNone(row["academic_period_id"])
            self.assertIsNotNone(row["academic_structure_version_id"])
        finally: db.close()


if __name__=="__main__":
    unittest.main()
