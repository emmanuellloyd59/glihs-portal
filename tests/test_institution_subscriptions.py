"""Regression tests for additive institutional lifecycle and subscription policy."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from institution_subscriptions import initialize_subscription_schema, effective_access


class SubscriptionFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        def connect():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            return db
        self.connect = connect
        db = connect()
        db.executescript("""
          CREATE TABLE schools(id INTEGER PRIMARY KEY, name TEXT);
          CREATE TABLE users(id INTEGER PRIMARY KEY);
          INSERT INTO schools(id,name) VALUES(1,'Existing School');
          INSERT INTO users(id) VALUES(1);
        """)
        db.commit()
        db.close()
        initialize_subscription_schema(connect)

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_initialization_is_idempotent(self):
        initialize_subscription_schema(self.connect)
        db=self.connect()
        try:
            names={r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"institution_lifecycle","subscription_plans","institution_subscriptions",
                             "billing_operations","billing_provider_events","subscription_audit"}.issubset(names))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM institution_lifecycle").fetchone()[0],0)
        finally: db.close()

    def test_existing_school_is_not_implicitly_enrolled_or_restricted(self):
        db=self.connect()
        try:
            policy=effective_access(db,1)
            self.assertEqual(policy["access"],"legacy_unmetered")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM institution_lifecycle").fetchone()[0],0)
        finally: db.close()

    def test_grace_period_is_read_only_only_after_deadline_without_state_mutation(self):
        db=self.connect()
        try:
            plan=db.execute("""INSERT INTO subscription_plans(code,name,billing_period,amount_minor,currency,
                         grace_days,entitlements_json,usage_limits_json) VALUES('starter','Starter','monthly',0,'USD',14,'{}','{}')""").lastrowid
            start=datetime.now(timezone.utc)-timedelta(days=30)
            end=start+timedelta(days=30)
            db.execute("""INSERT INTO institution_subscriptions
                (school_id,plan_id,status,billing_period,currency,amount_minor,period_start,period_end,grace_ends_at)
                VALUES(1,?,'past_due','monthly','USD',0,?,?,?)""",
                (plan,start.isoformat(),end.isoformat(),(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()))
            db.execute("INSERT INTO institution_lifecycle(school_id,state) VALUES(1,'active')")
            db.commit()
            policy=effective_access(db,1)
            self.assertEqual(policy["access"],"read_only")
            self.assertEqual(db.execute("SELECT state FROM institution_lifecycle WHERE school_id=1").fetchone()[0],"active")
        finally: db.close()

    def test_explicit_suspension_is_separate_from_subscription_status(self):
        db=self.connect()
        try:
            db.execute("INSERT INTO institution_lifecycle(school_id,state) VALUES(1,'suspended')")
            db.commit()
            self.assertEqual(effective_access(db,1)["access"],"essential_only")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schools").fetchone()[0],1)
        finally: db.close()

    def test_archived_state_does_not_delete_school_records(self):
        db=self.connect()
        try:
            db.execute("INSERT INTO institution_lifecycle(school_id,state) VALUES(1,'archived')")
            db.commit()
            self.assertEqual(effective_access(db,1)["access"],"none")
            self.assertEqual(db.execute("SELECT name FROM schools WHERE id=1").fetchone()[0],"Existing School")
        finally: db.close()


if __name__ == "__main__":
    unittest.main()
