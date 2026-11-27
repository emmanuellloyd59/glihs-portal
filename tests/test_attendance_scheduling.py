import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from attendance_scheduling import (
    _valid_date, _valid_time, _school_timezone, initialize_attendance_scheduling,
)


class AttendanceSchedulingValidationTests(unittest.TestCase):
    def test_date_validation_is_strict_iso(self):
        self.assertIsNotNone(_valid_date("2026-11-27"))
        self.assertIsNone(_valid_date("27-11-2026"))
        self.assertIsNone(_valid_date("2026-02-30"))

    def test_time_validation_is_strict_hhmm(self):
        self.assertTrue(_valid_time("08:05"))
        self.assertFalse(_valid_time("8:05"))
        self.assertFalse(_valid_time("25:00"))


    def test_student_attendance_is_unique_and_tenant_class_guarded(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        import os
        os.close(handle)
        def connect():
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db
        db = connect()
        db.executescript("""
          CREATE TABLE schools(id INTEGER PRIMARY KEY, timezone TEXT);
          CREATE TABLE users(id INTEGER PRIMARY KEY);
          CREATE TABLE students(id INTEGER PRIMARY KEY, school_id INTEGER, class_level TEXT);
          CREATE TABLE campuses(id INTEGER PRIMARY KEY, school_id INTEGER, timezone TEXT);
          CREATE TABLE academic_years(id INTEGER PRIMARY KEY, school_id INTEGER, starts_on TEXT, ends_on TEXT, status TEXT);
          CREATE TABLE academic_periods(id INTEGER PRIMARY KEY, school_id INTEGER, academic_year_id INTEGER, starts_on TEXT, ends_on TEXT, status TEXT);
          INSERT INTO schools VALUES(1,'Africa/Monrovia'),(2,'UTC');
          INSERT INTO users VALUES(1),(2);
          INSERT INTO students VALUES(1,1,'Grade 1'),(2,2,'Grade 1');
          INSERT INTO academic_years VALUES(1,1,'2026-01-01','2026-12-31','active');
          INSERT INTO academic_periods VALUES(1,1,1,'2026-01-01','2026-12-31','active');
        """)
        db.commit()
        db.close()
        try:
            initialize_attendance_scheduling(connect)
            db = connect()
            session = db.execute("""INSERT INTO attendance_sessions
              (school_id,academic_year_id,academic_period_id,class_level,session_date,created_by)
              VALUES(1,1,1,'Grade 1','2026-11-27',1)""").lastrowid
            db.execute("""INSERT INTO student_attendance_records
              (school_id,session_id,student_id,status,recorded_by)
              VALUES(1,?,1,'present',1)""", (session,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""INSERT INTO student_attendance_records
                  (school_id,session_id,student_id,status,recorded_by)
                  VALUES(1,?,1,'absent',1)""", (session,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""INSERT INTO student_attendance_records
                  (school_id,session_id,student_id,status,recorded_by)
                  VALUES(1,?,2,'present',1)""", (session,))
            db.close()
        finally:
            Path(path).unlink(missing_ok=True)

    def test_schema_initialization_is_idempotent_and_preserves_existing_rows(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        import os
        os.close(handle)
        def connect():
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db
        db = connect()
        db.executescript("""
          CREATE TABLE schools(id INTEGER PRIMARY KEY, timezone TEXT);
          CREATE TABLE users(id INTEGER PRIMARY KEY);
          CREATE TABLE students(id INTEGER PRIMARY KEY, school_id INTEGER, class_level TEXT);
          CREATE TABLE academic_years(id INTEGER PRIMARY KEY, school_id INTEGER, starts_on TEXT, ends_on TEXT, status TEXT);
          CREATE TABLE academic_periods(id INTEGER PRIMARY KEY, school_id INTEGER, academic_year_id INTEGER, starts_on TEXT, ends_on TEXT, status TEXT);
          INSERT INTO schools VALUES(1,'Africa/Monrovia');
          INSERT INTO users VALUES(1);
          INSERT INTO students VALUES(1,1,'Grade 1');
          INSERT INTO academic_years VALUES(1,1,'2026-01-01','2026-12-31','active');
          INSERT INTO academic_periods VALUES(1,1,1,'2026-01-01','2026-12-31','active');
        """)
        db.commit()
        db.close()
        try:
            initialize_attendance_scheduling(connect)
            initialize_attendance_scheduling(connect)
            db = connect()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM students").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attendance_sessions").fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE student_attendance_records SET status='absent' WHERE id=1")
            request_id = db.execute("""INSERT INTO attendance_correction_requests
              (school_id,record_type,record_id,old_status,new_status,reason,requested_by)
              VALUES(1,'student',1,'present','absent','Teacher entered the wrong status',1)""").lastrowid
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""UPDATE attendance_correction_requests
                  SET decision='approved',decided_by=1,decided_at=CURRENT_TIMESTAMP WHERE id=?""",(request_id,))
            db.execute("""UPDATE attendance_correction_requests
              SET decision='approved',decided_by=2,decided_at=CURRENT_TIMESTAMP WHERE id=?""",(request_id,))
            db.execute("UPDATE student_attendance_records SET status='absent' WHERE id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM student_attendance_records WHERE id=1")
            db.close()
        finally:
            Path(path).unlink(missing_ok=True)

    def test_timezone_uses_school_iana_zone(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        import os
        os.close(handle)
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE schools(id INTEGER PRIMARY KEY, timezone TEXT)")
        db.execute("INSERT INTO schools VALUES(1,'America/Los_Angeles')")
        db.commit()
        try:
            zone = _school_timezone(db, 1)
            from datetime import datetime, timezone
            utc_instant = datetime(2026, 11, 28, 1, 30, tzinfo=timezone.utc)
            self.assertEqual(utc_instant.astimezone(zone).date().isoformat(), "2026-11-27")
        finally:
            db.close()
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
