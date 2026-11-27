"""PostgreSQL-native migration tests against the disposable CI server."""
import os
import unittest
import uuid

from db.migrations import (
    Migration,
    MigrationError,
    POSTGRESQL_MIGRATIONS,
    apply_postgresql_migrations,
)
from db.postgresql import connect_postgresql


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is not configured")
class PostgreSQLMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect_postgresql(os.environ["TEST_POSTGRES_URL"])
        self.schema = "cmhs_mig_" + uuid.uuid4().hex
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        self.connection.commit()

    def tearDown(self):
        try:
            self.connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
            self.connection.commit()
        finally:
            self.connection.close()

    def test_fresh_install_and_replay_are_idempotent(self):
        applied = apply_postgresql_migrations(self.connection)
        self.assertEqual(applied, (1, 2))
        self.assertEqual(apply_postgresql_migrations(self.connection), ())

        tables = {
            row["tablename"]
            for row in self.connection.execute(
                """SELECT tablename FROM pg_tables
                   WHERE schemaname = current_schema()"""
            ).fetchall()
        }
        expected = {
            "schema_migrations", "schools", "users", "students",
            "parent_student_links", "student_finances", "grades",
            "report_cards", "attendance", "audit_log", "class_timetable",
            "teacher_class_assignments", "homework_assignments",
            "homework_submissions",
        }
        self.assertTrue(expected.issubset(tables))

        school_id = self.connection.execute(
            "INSERT INTO schools (name,slug) VALUES (%s,%s) RETURNING id",
            ("Migration Test School", "migration-test"),
        ).fetchone()["id"]
        user_id = self.connection.execute(
            """INSERT INTO users (username,password_hash,role,school_id)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            ("migration_teacher", "test-hash", "teacher", school_id),
        ).fetchone()["id"]
        student_id = self.connection.execute(
            """INSERT INTO students (admission_number,full_name,class_level,school_id)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            ("MIG-001", "Fictional Student", "Grade 7", school_id),
        ).fetchone()["id"]
        self.assertGreater(user_id, 0)
        self.assertGreater(student_id, 0)
        self.connection.rollback()

    def _seed_two_schools(self):
        # Each test owns a fresh isolated schema; install the real schema before seeding.
        apply_postgresql_migrations(self.connection)
        school_a = self.connection.execute(
            "INSERT INTO schools (name,slug) VALUES (%s,%s) RETURNING id",
            ("School A", "school-a-" + self.schema[-8:]),
        ).fetchone()["id"]
        school_b = self.connection.execute(
            "INSERT INTO schools (name,slug) VALUES (%s,%s) RETURNING id",
            ("School B", "school-b-" + self.schema[-8:]),
        ).fetchone()["id"]
        parent_id = self.connection.execute(
            """INSERT INTO users (username,password_hash,role,school_id)
               VALUES (%s,%s,'parent',%s) RETURNING id""",
            ("parent_" + self.schema[-8:], "hash", school_a),
        ).fetchone()["id"]
        teacher_id = self.connection.execute(
            """INSERT INTO users (username,password_hash,role,school_id)
               VALUES (%s,%s,'teacher',%s) RETURNING id""",
            ("teacher_" + self.schema[-8:], "hash", school_a),
        ).fetchone()["id"]
        student_id = self.connection.execute(
            """INSERT INTO students (admission_number,full_name,class_level,school_id)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            ("B-001", "Fictional Student", "Grade 7", school_b),
        ).fetchone()["id"]
        self.connection.commit()
        return school_a, school_b, parent_id, teacher_id, student_id

    def test_parent_student_link_cannot_cross_school_boundary(self):
        school_a, _, parent_id, _, student_id = self._seed_two_schools()
        with self.assertRaises(Exception):
            self.connection.execute(
                """INSERT INTO parent_student_links
                   (parent_user_id,student_id,school_id)
                   VALUES (%s,%s,%s)""",
                (parent_id, student_id, school_a),
            )
        self.connection.rollback()

    def test_grade_cannot_be_recorded_for_another_school_student(self):
        school_a, _, _, teacher_id, student_id = self._seed_two_schools()
        with self.assertRaises(Exception):
            self.connection.execute(
                """INSERT INTO grades
                   (student_id,subject,assessment,score,max_score,recorded_by,school_id)
                   VALUES (%s,'Mathematics','Quiz 1',80,100,%s,%s)""",
                (student_id, teacher_id, school_a),
            )
        self.connection.rollback()

    def test_student_with_records_cannot_be_moved_to_another_school(self):
        school_a, school_b, _, teacher_id, student_id = self._seed_two_schools()
        # Attach a school-A grade to a valid school-A student, then verify the
        # school-B student cannot be reassigned while it has a school-scoped link.
        school_a_student = self.connection.execute(
            """INSERT INTO students (admission_number,full_name,class_level,school_id)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            ("A-REASSIGN", "Protected Student", "Grade 7", school_a),
        ).fetchone()["id"]
        self.connection.execute(
            """INSERT INTO grades
               (student_id,subject,assessment,score,max_score,recorded_by,school_id)
               VALUES (%s,'English','Quiz 1',75,100,%s,%s)""",
            (school_a_student, teacher_id, school_a),
        )
        # Link a finance row to the school-B student so moving it is rejected.
        school_b_teacher = self.connection.execute(
            """INSERT INTO users (username,password_hash,role,school_id)
               VALUES (%s,%s,'teacher',%s) RETURNING id""",
            ("teacher_b_" + self.schema[-8:], "hash", school_b),
        ).fetchone()["id"]
        self.connection.execute(
            """INSERT INTO student_finances
               (student_id,academic_year,total_fee,updated_by,school_id)
               VALUES (%s,'2026/2027',500,%s,%s)""",
            (student_id, school_b_teacher, school_b),
        )
        with self.assertRaises(Exception):
            self.connection.execute(
                "UPDATE students SET school_id=%s WHERE id=%s",
                (school_a, student_id),
            )
        self.connection.rollback()

    def test_user_with_student_account_link_cannot_change_school(self):
        school_a, school_b, _, _, _ = self._seed_two_schools()
        student_user_id = self.connection.execute(
            """INSERT INTO users (username,password_hash,role,school_id)
               VALUES (%s,%s,'student',%s) RETURNING id""",
            ("student_user_" + self.schema[-8:], "hash", school_a),
        ).fetchone()["id"]
        self.connection.execute(
            """INSERT INTO students
               (admission_number,full_name,class_level,account_user_id,school_id)
               VALUES (%s,%s,%s,%s,%s)""",
            ("A-ACCOUNT", "Account Student", "Grade 7", student_user_id, school_a),
        )
        with self.assertRaises(Exception):
            self.connection.execute(
                "UPDATE users SET school_id=%s WHERE id=%s",
                (school_b, student_user_id),
            )
        self.connection.rollback()

    def test_super_admin_can_be_a_school_scoped_finance_actor(self):
        school_a, _, _, _, _ = self._seed_two_schools()
        admin_id = self.connection.execute(
            """INSERT INTO users
               (username,password_hash,role,is_super_admin,school_id)
               VALUES (%s,%s,'super_admin',1,NULL) RETURNING id""",
            ("root_" + self.schema[-8:], "hash"),
        ).fetchone()["id"]
        student_id = self.connection.execute(
            """INSERT INTO students (admission_number,full_name,class_level,school_id)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            ("A-001", "School A Student", "Grade 8", school_a),
        ).fetchone()["id"]
        self.connection.execute(
            """INSERT INTO student_finances
               (student_id,academic_year,total_fee,updated_by,school_id)
               VALUES (%s,'2026/2027',1000,%s,%s)""",
            (student_id, admin_id, school_a),
        )
        self.connection.rollback()

    def test_modified_migration_checksum_fails_closed(self):
        apply_postgresql_migrations(self.connection)
        self.connection.execute(
            "UPDATE schema_migrations SET checksum=%s WHERE version=1",
            ("0" * 64,),
        )
        self.connection.commit()
        with self.assertRaisesRegex(MigrationError, "Unknown or modified"):
            apply_postgresql_migrations(self.connection)

    def test_failed_migration_rolls_back_ddl_and_ledger(self):
        migration = Migration(
            1,
            "deliberately-failing-test-migration",
            (
                "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            ),
        )
        with self.assertRaises(Exception):
            apply_postgresql_migrations(self.connection, (migration,))
        relation = self.connection.execute(
            "SELECT to_regclass(%s) AS relation",
            (f"{self.schema}.schema_migrations",),
        ).fetchone()["relation"]
        self.assertIsNone(relation)
        table = self.connection.execute(
            """SELECT to_regclass(%s) AS relation""",
            (f"{self.schema}.should_rollback",),
        ).fetchone()["relation"]
        self.assertIsNone(table)

    def test_migration_batch_rolls_back_all_pending_versions(self):
        migrations = (
            Migration(1, "first-transactional-migration", (
                "CREATE TABLE first_batch_table (id INTEGER PRIMARY KEY)",
            )),
            Migration(2, "second-failing-migration", (
                "CREATE TABLE second_batch_table (id INTEGER PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            )),
        )
        with self.assertRaises(Exception):
            apply_postgresql_migrations(self.connection, migrations)
        for table in ("schema_migrations", "first_batch_table", "second_batch_table"):
            relation = self.connection.execute(
                "SELECT to_regclass(%s) AS relation",
                (f"{self.schema}.{table}",),
            ).fetchone()["relation"]
            self.assertIsNone(relation, table)

    def test_unknown_ledger_version_is_rejected(self):
        apply_postgresql_migrations(self.connection)
        self.connection.execute(
            """INSERT INTO schema_migrations (version,name,checksum)
               VALUES (99,'unrecognized',%s)""",
            ("f" * 64,),
        )
        self.connection.commit()
        with self.assertRaisesRegex(MigrationError, "gap or unsupported"):
            apply_postgresql_migrations(self.connection)


class PostgreSQLMigrationDefinitionTests(unittest.TestCase):
    def test_initial_migration_is_explicitly_postgresql_native(self):
        migration = POSTGRESQL_MIGRATIONS[0]
        sql = "\\n".join(migration.statements)
        self.assertEqual(migration.version, 1)
        self.assertIn("GENERATED BY DEFAULT AS IDENTITY", sql)
        self.assertIn("TIMESTAMPTZ", sql)
        self.assertNotIn("AUTOINCREMENT", sql)
        self.assertNotIn("PRAGMA", sql)
        self.assertNotIn("sqlite_master", sql)


if __name__ == "__main__":
    unittest.main()
