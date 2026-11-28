import sqlite3
import json
import tempfile
import unittest
from pathlib import Path

from assessment_engine import (
    calculate_weighted_result, initialize_assessment_schema, validate_grade_bands,
)

BANDS=[
 {"minimum":0,"maximum":50,"grade":"F","remark":"Needs improvement"},
 {"minimum":50,"maximum":70,"grade":"C","remark":"Satisfactory"},
 {"minimum":70,"maximum":90,"grade":"B","remark":"Good"},
 {"minimum":90,"maximum":100,"grade":"A","remark":"Excellent"},
]

class AssessmentCalculationTests(unittest.TestCase):
    def test_weighted_calculation_normalizes_weights(self):
        result=calculate_weighted_result([
          {"assessment_id":1,"score":"8","max_score":"10","weight":"40"},
          {"assessment_id":2,"score":"90","max_score":"100","weight":"60"},
        ],BANDS)
        self.assertEqual(result["percentage"],"86.00")
        self.assertEqual(result["grade"],"B")

    def test_half_up_rounding(self):
        result=calculate_weighted_result([
          {"assessment_id":1,"score":"1.005","max_score":"1.01","weight":"1"}
        ],[{"minimum":0,"maximum":100,"grade":"PASS"}])
        self.assertEqual(result["percentage"],"99.50")

    def test_zero_and_full_marks_boundaries(self):
        self.assertEqual(calculate_weighted_result([
          {"assessment_id":1,"score":0,"max_score":10,"weight":1}
        ],BANDS)["grade"],"F")
        self.assertEqual(calculate_weighted_result([
          {"assessment_id":1,"score":10,"max_score":10,"weight":1}
        ],BANDS)["grade"],"A")

    def test_rejects_gaps_and_overlaps(self):
        with self.assertRaises(ValueError):
            validate_grade_bands([{"minimum":0,"maximum":40,"grade":"F"},{"minimum":50,"maximum":100,"grade":"A"}])
        with self.assertRaises(ValueError):
            validate_grade_bands([{"minimum":0,"maximum":60,"grade":"F"},{"minimum":50,"maximum":100,"grade":"A"}])

    def test_rejects_out_of_range_score_and_zero_weight(self):
        with self.assertRaises(ValueError):
            calculate_weighted_result([{"assessment_id":1,"score":11,"max_score":10,"weight":1}],BANDS)
        with self.assertRaises(ValueError):
            calculate_weighted_result([{"assessment_id":1,"score":1,"max_score":10,"weight":0}],BANDS)

    def test_rejects_non_finite_values(self):
        with self.assertRaises(ValueError):
            calculate_weighted_result([{"assessment_id":1,"score":"NaN","max_score":10,"weight":1}],BANDS)

class AssessmentSchemaTests(unittest.TestCase):
    def test_schema_is_idempotent_and_tenant_trigger_blocks_cross_school_score(self):
        handle,path=tempfile.mkstemp(suffix=".sqlite3")
        import os
        os.close(handle)
        def connect():
            db=sqlite3.connect(path)
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db
        db=connect()
        try:
            db.executescript("""
              CREATE TABLE students(id INTEGER PRIMARY KEY,school_id INTEGER NOT NULL,status TEXT DEFAULT 'active',class_level TEXT);
              INSERT INTO students(id,school_id,class_level) VALUES(1,10,'Grade 7'),(2,20,'Grade 7');
            """)
            db.commit()
        finally: db.close()
        try:
            initialize_assessment_schema(connect)
            initialize_assessment_schema(connect)
            db=connect()
            db.execute("""INSERT INTO configurable_assessments
              (school_id,title,assessment_type,subject,class_level,academic_year,period_name,weight,max_score,created_by)
              VALUES(10,'Quiz','continuous_assessment','Math','Grade 7','2026-27','Term 1','20','10',1)""")
            assessment=db.execute("SELECT id FROM configurable_assessments").fetchone()["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("""INSERT INTO assessment_scores
                  (school_id,assessment_id,student_id,score,recorded_by) VALUES(10,?,2,'8',1)""",(assessment,))
            db.close()
        finally: Path(path).unlink(missing_ok=True)


class _AssessmentTestUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False
    role = "school_admin"
    is_super_admin = False

    def __init__(self, user_id=1):
        self.id = user_id

    def get_id(self):
        return str(self.id)


class AssessmentSubjectIntegrationTests(unittest.TestCase):
    def setUp(self):
        import os
        from flask import Flask
        from flask_login import LoginManager
        from assessment_engine import create_assessment_blueprint

        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)

        def connect():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db

        self.connect = connect
        db = connect()
        db.executescript("""
          CREATE TABLE students(
            id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', class_level TEXT NOT NULL
          );
          INSERT INTO students(id,school_id,status,class_level)
            VALUES(1,7,'active','Grade 8');
        """)
        db.commit()
        db.close()
        initialize_assessment_schema(connect)

        db = connect()
        bands = [
            {"minimum":"0","maximum":"50","grade":"F","remark":"Fail"},
            {"minimum":"50","maximum":"70","grade":"C","remark":"Pass"},
            {"minimum":"70","maximum":"90","grade":"B","remark":"Good"},
            {"minimum":"90","maximum":"100","grade":"A","remark":"Excellent"},
        ]
        db.execute("""INSERT INTO assessment_grading_scales
          (school_id,name,version,bands_json,created_by) VALUES(7,'School Scale',1,?,1)""",
          (json.dumps(bands),))
        for title, subject, score, weight in [
            ("Math test","Mathematics","80","100"),
            ("Science test","Science","60","200"),
        ]:
            cur = db.execute("""INSERT INTO configurable_assessments
              (school_id,title,assessment_type,subject,class_level,academic_year,period_name,
               weight,max_score,created_by)
              VALUES(7,?,'examination',?,'Grade 8','2026-2027','Term 1',?,'100',1)""",
              (title,subject,weight))
            db.execute("""INSERT INTO assessment_scores
              (school_id,assessment_id,student_id,score,recorded_by)
              VALUES(7,?,1,?,1)""", (cur.lastrowid,score))
        db.commit()
        db.close()

        self.app = Flask(__name__)
        self.app.secret_key = "assessment-test-secret"
        self.app.config["TESTING"] = True
        login_manager = LoginManager(self.app)
        login_manager.user_loader(lambda uid: _AssessmentTestUser(int(uid)) if uid in {"1","2"} else None)
        self.app.register_blueprint(create_assessment_blueprint(connect, lambda: 7))
        self.client = self.app.test_client()
        self._login(1)

    def tearDown(self):
        import os
        os.unlink(self.path)

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_calculation_keeps_each_subject_separate(self):
        response = self.client.post("/api/assessment/results/calculate", json={
            "student_id":1,"academic_year":"2026-2027","period_name":"Term 1",
            "grading_scale_id":1,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.json["result"]
        by_subject = {item["subject"]: item for item in result["subjects"]}
        self.assertEqual(by_subject["Mathematics"]["percentage"], "80.00")
        self.assertEqual(by_subject["Mathematics"]["grade"], "B")
        self.assertEqual(by_subject["Science"]["percentage"], "60.00")
        self.assertEqual(by_subject["Science"]["grade"], "C")
        self.assertEqual(result["overall"]["percentage"], "70.00")
        db = self.connect()
        try:
            stored = db.execute("SELECT calculation_snapshot_json FROM assessment_results").fetchone()
            snapshot = json.loads(stored["calculation_snapshot_json"])
            self.assertEqual({item["subject"] for item in snapshot["subjects"]},
                             {"Mathematics","Science"})
            audit_count = db.execute("SELECT COUNT(*) FROM assessment_audit_events WHERE action='calculated'").fetchone()[0]
            self.assertEqual(audit_count, 1)
            self.assertEqual(result["overall"]["aggregation"], "equal_weighted_subject_average")
        finally:
            db.close()

    def test_correction_grade_must_match_frozen_scale(self):
        db = self.connect()
        bands = [
            {"minimum":"0","maximum":"50","grade":"F","remark":"Fail"},
            {"minimum":"50","maximum":"70","grade":"C","remark":"Pass"},
            {"minimum":"70","maximum":"90","grade":"B","remark":"Good"},
            {"minimum":"90","maximum":"100","grade":"A","remark":"Excellent"},
        ]
        snapshot = {"scale_id":1,"scale_name":"School Scale","scale_version":1,"bands":bands}
        calculation = {
            "overall":{"percentage":"80.00","grade":"B","remark":"Good"},
            "subjects":[{"subject":"Mathematics","percentage":"80.00","grade":"B","remark":"Good","comments":""}],
        }
        db.execute("""INSERT INTO assessment_results
          (school_id,student_id,academic_year,period_name,grading_scale_id,grading_scale_version,
           grading_snapshot_json,calculation_snapshot_json,percentage,grade,status,issued_at,issued_by)
          VALUES(7,1,'2026-2027','Term 2',1,1,?,?, '80.00','B','issued','2026-11-28T00:00:00+00:00',2)""",
          (json.dumps(snapshot),json.dumps(calculation)))
        db.commit()
        db.close()
        response = self.client.post("/api/assessment/results/1/corrections", json={
            "percentage":"80","grade":"A","reason":"Attempted invalid grade boundary",
        })
        self.assertEqual(response.status_code, 400)


    def test_subject_correction_grade_must_match_frozen_scale(self):
        db = self.connect()
        bands = [
            {"minimum":"0","maximum":"50","grade":"F","remark":"Fail"},
            {"minimum":"50","maximum":"70","grade":"C","remark":"Pass"},
            {"minimum":"70","maximum":"90","grade":"B","remark":"Good"},
            {"minimum":"90","maximum":"100","grade":"A","remark":"Excellent"},
        ]
        snapshot = {"scale_id":1,"scale_name":"School Scale","scale_version":1,"bands":bands}
        calculation = {
            "overall":{"percentage":"80.00","grade":"B","remark":"Good"},
            "subjects":[{"subject":"Mathematics","percentage":"80.00","grade":"B","remark":"Good","comments":""}],
        }
        db.execute("""INSERT INTO assessment_results
          (school_id,student_id,academic_year,period_name,grading_scale_id,grading_scale_version,
           grading_snapshot_json,calculation_snapshot_json,percentage,grade,status,issued_at,issued_by)
          VALUES(7,1,'2026-2027','Term 3',1,1,?,?, '80.00','B','issued','2026-11-28T00:00:00+00:00',2)""",
          (json.dumps(snapshot),json.dumps(calculation)))
        db.commit()
        db.close()
        response = self.client.post("/api/assessment/results/2/corrections", json={
            "percentage":"80","grade":"B","reason":"Invalid subject grade",
            "subject_results":[{"subject":"Mathematics","percentage":"80","grade":"A"}],
        })
        self.assertEqual(response.status_code, 400)

    def test_correction_table_additive_migration_is_idempotent(self):
        initialize_assessment_schema(self.connect)
        initialize_assessment_schema(self.connect)
        db = self.connect()
        try:
            columns = {row["name"] for row in db.execute(
                "PRAGMA table_info(assessment_result_corrections)").fetchall()}
            self.assertIn("proposed_subject_results_json", columns)
        finally:
            db.close()



if __name__ == "__main__":
    unittest.main()
