import sqlite3
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

if __name__=="__main__": unittest.main()
