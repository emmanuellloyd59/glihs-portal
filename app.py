import os
import csv
import secrets
import sqlite3
from pathlib import Path
import uuid
from werkzeug.utils import secure_filename
from flask import send_file
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import time

from flask import Flask, render_template, redirect, url_for, request, flash, abort, session, jsonify
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash


# --------------------------------------------------
# 1. APPLICATION CONFIGURATION
# --------------------------------------------------

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
DATABASE = Path(os.environ.get("DATABASE_PATH", str(INSTANCE_DIR / "glihs.db")))
DATABASE.parent.mkdir(parents=True, exist_ok=True)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "SECRET_KEY is missing. Configure this environment variable "
        "in the hosting provider or Codespaces, then restart the app."
    )

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
    "SESSION_COOKIE_SECURE", "true"
).strip().lower() in ("1", "true", "yes", "on")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
# School records can contain sensitive information; do not let browsers or
# intermediary caches retain rendered pages, and cap request bodies to limit abuse.
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."


@app.after_request
def add_security_headers(response):
    """Apply baseline browser security headers to every application response."""
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # Authenticated responses can contain sensitive student, guardian, or finance data.
    if current_user.is_authenticated:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
    return response


STAFF_ROLES = {
    "school_admin": "School Administrator",
    "principal": "Principal",
    "vice_principal": "Vice Principal",
    "registrar": "Registrar / Student Records Officer",
    "admissions_officer": "Admissions Officer",
    "academic_admin": "Academic Administrator",
    "attendance_officer": "Attendance Officer",
    "teacher": "Teacher",
    "finance_admin": "Accountant / Bursar",
    "librarian": "Librarian / Learning Resources Officer",
    "counselor": "School Counselor",
    "parent": "Parent / Guardian (child link required)",
    "student": "Student (enrollment link required)",
}
STUDENT_RECORD_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal", "registrar",
    "academic_admin", "teacher", "admissions_officer", "attendance_officer",
}


# --------------------------------------------------
# 2. DATABASE CONNECTION AND SAFE SCHEMA MIGRATION
# --------------------------------------------------

def get_db_connection():
    """Open a row-aware SQLite connection with referential integrity enabled."""
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate_users_table(connection):
    """Upgrade the original three-role users table without losing user records."""
    schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    schema_sql = (schema_row["sql"] or "") if schema_row else ""
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    required_role_tokens = (
        "'super_admin'", "'school_admin'", "'principal'", "'vice_principal'",
        "'registrar'", "'admissions_officer'", "'academic_admin'",
        "'attendance_officer'", "'finance_admin'", "'librarian'",
        "'counselor'", "'teacher'", "'parent'", "'student'",
    )
    if (
        all(token in schema_sql for token in required_role_tokens)
        and {"status", "is_super_admin", "created_at", "approved_by", "approved_at", "full_name", "school_id"}.issubset(columns)
    ):
        return

    connection.execute("DROP TABLE IF EXISTS users_migrating")
    connection.execute(
        """
        CREATE TABLE users_migrating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (
                role IN ('super_admin', 'school_admin', 'principal', 'vice_principal',
                         'registrar', 'admissions_officer', 'academic_admin',
                         'attendance_officer', 'finance_admin', 'librarian',
                         'counselor', 'teacher', 'parent', 'student')
            ),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('pending', 'active', 'suspended')),
            is_super_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_super_admin IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_by INTEGER,
            approved_at TEXT,
            school_id INTEGER REFERENCES schools(id)
        )
        """
    )
    if schema_row:
        old_columns = columns
        status_expr = "status" if "status" in old_columns else "'active'"
        super_expr = (
            "is_super_admin" if "is_super_admin" in old_columns
            else "CASE WHEN role = 'admin' THEN 1 ELSE 0 END"
        )
        created_expr = "created_at" if "created_at" in old_columns else "CURRENT_TIMESTAMP"
        approved_by_expr = "approved_by" if "approved_by" in old_columns else "NULL"
        approved_at_expr = "approved_at" if "approved_at" in old_columns else "NULL"
        full_name_expr = "full_name" if "full_name" in old_columns else "username"
        school_expr = "school_id" if "school_id" in old_columns else "NULL"
        connection.execute(
            f"""
            INSERT INTO users_migrating
                (id, username, full_name, password_hash, role, status, is_super_admin,
                 created_at, approved_by, approved_at, school_id)
            SELECT id, username, {full_name_expr}, password_hash,
                   CASE WHEN role = 'admin' THEN 'super_admin' ELSE role END,
                   {status_expr},
                   CASE WHEN role = 'admin' THEN 1 ELSE {super_expr} END,
                   {created_expr}, {approved_by_expr}, {approved_at_expr}, {school_expr}
            FROM users
            """
        )
        connection.execute("DROP TABLE users")
    connection.execute("ALTER TABLE users_migrating RENAME TO users")


def migrate_grade_assessment_uniqueness(connection):
    """Allow the same assessment label in different terms while preserving grade rows."""
    unique_indexes = connection.execute("PRAGMA index_list(grades)").fetchall()
    legacy_constraint_found = False
    for index in unique_indexes:
        if not index["unique"]:
            continue
        quoted_index_name = index["name"].replace("'", "''")
        index_columns = connection.execute(
            "PRAGMA index_info('" + quoted_index_name + "')"
        ).fetchall()
        column_names = tuple(row["name"] for row in index_columns)
        if column_names == ("student_id", "subject", "assessment"):
            legacy_constraint_found = True
            break

    if not legacy_constraint_found:
        return

    connection.execute("DROP TABLE IF EXISTS grades_migrating")
    connection.execute(
        """
        CREATE TABLE grades_migrating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id),
            subject TEXT NOT NULL,
            assessment TEXT NOT NULL,
            score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
            max_score REAL NOT NULL CHECK (max_score > 0 AND score <= max_score),
            recorded_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            academic_year TEXT NOT NULL DEFAULT 'Unassigned',
            term TEXT NOT NULL DEFAULT 'Unassigned',
            school_id INTEGER REFERENCES schools(id),
            UNIQUE(student_id, subject, assessment, academic_year, term)
        )
        """
    )
    old_grade_columns = {row["name"] for row in connection.execute("PRAGMA table_info(grades)").fetchall()}
    school_expr = "school_id" if "school_id" in old_grade_columns else "NULL"
    connection.execute(
        f"""
        INSERT INTO grades_migrating
            (id, student_id, subject, assessment, score, max_score,
             recorded_by, created_at, academic_year, term, school_id)
        SELECT id, student_id, subject, assessment, score, max_score,
               recorded_by, created_at, academic_year, term, {school_expr}
        FROM grades
        """
    )
    connection.execute("DROP TABLE grades")
    connection.execute("ALTER TABLE grades_migrating RENAME TO grades")



def migrate_student_admission_number_uniqueness(connection):
    """Replace global admission-number uniqueness with per-school uniqueness.

    Existing student IDs and every known student field are preserved. This
    migration runs with foreign-key enforcement temporarily disabled by
    initialize_database(), then restores the partial indexes used by the app.
    """
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'students'"
    ).fetchone()
    if not table or not table["sql"] or "admission_number TEXT NOT NULL UNIQUE" not in table["sql"]:
        return

    connection.execute("""
        CREATE TABLE students_migrating (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admission_number TEXT NOT NULL,
            full_name TEXT NOT NULL,
            class_level TEXT NOT NULL,
            date_of_birth TEXT,
            guardian_name TEXT,
            guardian_phone TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            account_user_id INTEGER REFERENCES users(id),
            enrollment_date TEXT,
            previous_school TEXT,
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            record_notes TEXT,
            student_id_number TEXT,
            school_id INTEGER REFERENCES schools(id),
            UNIQUE (school_id, admission_number)
        )
    """)
    columns = (
        "id, admission_number, full_name, class_level, date_of_birth, "
        "guardian_name, guardian_phone, status, created_at, account_user_id, "
        "enrollment_date, previous_school, emergency_contact_name, "
        "emergency_contact_phone, record_notes, student_id_number, school_id"
    )
    connection.execute(
        f"INSERT INTO students_migrating ({columns}) SELECT {columns} FROM students"
    )
    connection.execute("DROP TABLE students")
    connection.execute("ALTER TABLE students_migrating RENAME TO students")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_account_user_id "
        "ON students(account_user_id) WHERE account_user_id IS NOT NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_student_id_number "
        "ON students(student_id_number) WHERE student_id_number IS NOT NULL"
    )


def initialize_database():
    """Create or upgrade tables without intentionally resetting existing records."""
    connection = get_db_connection()
    try:
        # The legacy users-table migration replaces the table. Temporarily disable
        # FK enforcement for that schema operation, then re-enable it for normal use.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (
                    role IN ('super_admin', 'school_admin', 'principal', 'vice_principal', 'registrar',
                         'admissions_officer', 'academic_admin', 'attendance_officer',
                         'finance_admin', 'librarian', 'counselor', 'teacher', 'parent', 'student')
                ),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('pending', 'active', 'suspended')),
                is_super_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_super_admin IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approved_by INTEGER,
                approved_at TEXT
            )
            """
        )
        migrate_users_table(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_number TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                class_level TEXT NOT NULL,
                date_of_birth TEXT,
                guardian_name TEXT,
                guardian_phone TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        student_columns = {row["name"] for row in connection.execute("PRAGMA table_info(students)").fetchall()}
        if "account_user_id" not in student_columns:
            connection.execute("ALTER TABLE students ADD COLUMN account_user_id INTEGER REFERENCES users(id)")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_account_user_id "
            "ON students(account_user_id) WHERE account_user_id IS NOT NULL"
        )
        # Parent-to-child relationships are explicitly school-scoped and can be
        # many-to-many (one parent may have multiple children; a child may have
        # multiple verified guardians).
        connection.execute("""
            CREATE TABLE IF NOT EXISTS parent_student_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_user_id INTEGER NOT NULL REFERENCES users(id),
                student_id INTEGER NOT NULL REFERENCES students(id),
                school_id INTEGER NOT NULL REFERENCES schools(id),
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(parent_user_id, student_id)
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_parent_student_links_school "
            "ON parent_student_links(school_id, parent_user_id, student_id)"
        )
        student_columns = {row["name"] for row in connection.execute("PRAGMA table_info(students)").fetchall()}
        for column_name, column_type in (
            ("enrollment_date", "TEXT"),
            ("previous_school", "TEXT"),
            ("emergency_contact_name", "TEXT"),
            ("emergency_contact_phone", "TEXT"),
            ("record_notes", "TEXT"),
        ):
            if column_name not in student_columns:
                connection.execute(f"ALTER TABLE students ADD COLUMN {column_name} {column_type}")
        if "student_id_number" not in student_columns:
            connection.execute("ALTER TABLE students ADD COLUMN student_id_number TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_student_id_number "
            "ON students(student_id_number) WHERE student_id_number IS NOT NULL"
        )
        connection.execute(
            """UPDATE students
               SET student_id_number = 'CMHS-' || strftime('%Y', 'now') || '-' || printf('%05d', id)
               WHERE student_id_number IS NULL"""
        )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS student_finances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                academic_year TEXT NOT NULL,
                total_fee REAL NOT NULL CHECK (total_fee >= 0),
                amount_paid REAL NOT NULL DEFAULT 0 CHECK (amount_paid >= 0 AND amount_paid <= total_fee),
                notes TEXT,
                updated_by INTEGER NOT NULL REFERENCES users(id),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, academic_year)
            )
        """)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS grades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                subject TEXT NOT NULL,
                assessment TEXT NOT NULL,
                score REAL NOT NULL CHECK (score >= 0 AND score <= 100),
                max_score REAL NOT NULL CHECK (max_score > 0 AND score <= max_score),
                recorded_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                academic_year TEXT NOT NULL DEFAULT 'Unassigned',
                term TEXT NOT NULL DEFAULT 'Unassigned',
                UNIQUE(student_id, subject, assessment, academic_year, term)
            )
            """
        )
        grade_columns = {row["name"] for row in connection.execute("PRAGMA table_info(grades)").fetchall()}
        if "academic_year" not in grade_columns:
            connection.execute("ALTER TABLE grades ADD COLUMN academic_year TEXT NOT NULL DEFAULT 'Unassigned'")
        if "term" not in grade_columns:
            connection.execute("ALTER TABLE grades ADD COLUMN term TEXT NOT NULL DEFAULT 'Unassigned'")
        migrate_grade_assessment_uniqueness(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS report_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                academic_year TEXT NOT NULL,
                term TEXT NOT NULL CHECK (term IN ('Term 1', 'Term 2', 'Term 3')),
                published INTEGER NOT NULL DEFAULT 0 CHECK (published IN (0, 1)),
                published_by INTEGER REFERENCES users(id),
                published_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, academic_year, term)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id),
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('present', 'absent', 'late', 'excused')),
                note TEXT,
                recorded_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, attendance_date)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                target_username TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Version 0.3 foundation: establish an explicit school owner for each
        # school's records. This migration is additive and idempotent so existing
        # demo data is retained. Route/query enforcement remains a separate required
        # release gate; this schema change alone does NOT make multi-school use safe.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute(
            "INSERT INTO schools (id, name, slug) VALUES (1, 'CMHS', 'legacy-demo-campus') "
            "ON CONFLICT(id) DO NOTHING"
        )
        # Rename only the exact legacy placeholder school. Keep its stable ID,
        # slug, status, and all related records so tenant links remain unchanged.
        connection.execute(
            "UPDATE schools SET name = 'CMHS' "
            "WHERE slug = 'legacy-demo-campus' AND name IN ('Legacy Demo Campus', 'GLIHS')"
        )
        owned_tables = (
            "users", "students", "student_finances", "grades",
            "report_cards", "attendance", "audit_log",
        )
        for table_name in owned_tables:
            table_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(" + table_name + ")"
                ).fetchall()
            }
            if "school_id" not in table_columns:
                connection.execute(
                    "ALTER TABLE " + table_name +
                    " ADD COLUMN school_id INTEGER REFERENCES schools(id)"
                )
            # Preserve legacy rows under one explicit school. Platform super-admins
            # remain platform-level and intentionally have no school assignment.
            if table_name == "users":
                connection.execute(
                    "UPDATE users SET school_id = 1 "
                    "WHERE school_id IS NULL AND is_super_admin = 0"
                )
            else:
                connection.execute(
                    "UPDATE " + table_name + " SET school_id = 1 WHERE school_id IS NULL"
                )

        # Remove the legacy global admission-number constraint only after all
        # records have a school owner, so the new constraint can be tenant-scoped.
        migrate_student_admission_number_uniqueness(connection)

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_school_role ON users(school_id, role)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_students_school_class ON students(school_id, class_level)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_grades_school_student_term "
            "ON grades(school_id, student_id, academic_year, term)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_attendance_school_student_date "
            "ON attendance(school_id, student_id, attendance_date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_finances_school_student_year "
            "ON student_finances(school_id, student_id, academic_year)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_cards_school_student_term "
            "ON report_cards(school_id, student_id, academic_year, term)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_school_created "
            "ON audit_log(school_id, created_at)"
        )

        # Optional one-time deployment bootstrap. Never modify an existing account
        # on restart: doing so could reset a password or elevate a non-admin user.
        # Use a fresh, unique username for initial setup, then remove both variables.
        initial_username = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
        initial_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
        if initial_username and initial_password:
            if (
                len(initial_username) <= 64
                and initial_username.replace("_", "").replace("-", "").isalnum()
                and len(initial_password) >= 12
            ):
                connection.execute(
                    """
                    INSERT INTO users
                        (username, full_name, password_hash, role, status, is_super_admin, approved_at)
                    VALUES (?, ?, ?, 'super_admin', 'active', 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(username) DO NOTHING
                    """,
                    (initial_username, initial_username, generate_password_hash(initial_password)),
                )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS class_timetable (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL REFERENCES schools(id),
                class_level TEXT NOT NULL,
                subject TEXT NOT NULL,
                teacher_id INTEGER NOT NULL REFERENCES users(id),
                weekday TEXT NOT NULL CHECK (weekday IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')),
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                room TEXT NOT NULL DEFAULT '',
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (start_time < end_time)
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_timetable_school_day_time "
            "ON class_timetable(school_id, weekday, start_time, end_time)"
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
    finally:
        connection.close()


initialize_database()


def seed_demo_students_if_enabled():
    """Load fictional student and financial data only into the dedicated demo school."""
    if os.environ.get("ENABLE_DEMO_STUDENT_SEED", "").strip() != "1":
        return
    target_slug = os.environ.get("DEMO_STUDENT_SCHOOL_SLUG", "").strip()
    if target_slug != "legacy-demo-campus":
        raise RuntimeError(
            "Demo data seeding is restricted to school slug 'legacy-demo-campus'."
        )

    csv_path = BASE_DIR / "data" / "demo_students_k2_grade12.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    if len(records) != 250:
        raise RuntimeError(f"Expected 250 demo students; found {len(records)}.")

    valid_levels = {"K2", "K3", "K4", *(f"Grade {i}" for i in range(1, 13))}
    for row in records:
        if (not row.get("admission_number", "").startswith("DEMO-K12-")
                or row.get("class_level") not in valid_levels
                or row.get("status") != "active"
                or not row.get("full_name", "").strip()):
            raise RuntimeError("Demo student CSV failed validation; no records were seeded.")

    connection = get_db_connection()
    try:
        school = connection.execute(
            "SELECT id FROM schools WHERE slug=? AND status='active'",
            (target_slug,),
        ).fetchone()
        if not school:
            raise RuntimeError("The designated demo school is not active; no demo data was seeded.")
        school_id = school["id"]

        existing = connection.execute(
            "SELECT COUNT(*) FROM students WHERE school_id=? AND admission_number LIKE 'DEMO-K12-%'",
            (school_id,),
        ).fetchone()[0]
        if existing not in (0, 250):
            raise RuntimeError(
                f"Found {existing} DEMO-K12 students; refusing partial or duplicate seed."
            )

        # A real user ID is required by the finance table, but all notes identify
        # these as fictional sample figures rather than actual payment receipts.
        updater = connection.execute(
            """SELECT id FROM users WHERE status='active'
               ORDER BY CASE WHEN school_id=? THEN 0 ELSE 1 END, id LIMIT 1""",
            (school_id,),
        ).fetchone()

        connection.execute("BEGIN IMMEDIATE")
        if existing == 0:
            connection.executemany(
                """INSERT INTO students
                   (admission_number, full_name, class_level, status, school_id)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (row["admission_number"], row["full_name"], row["class_level"],
                     row["status"], school_id)
                    for row in records
                ],
            )

        # Give the fictional cohort varied registration dates and a readable
        # date in the Student Records table. Only DEMO-K12 rows are touched.
        demo_students = connection.execute(
            """SELECT id, admission_number FROM students
               WHERE school_id=? AND admission_number LIKE 'DEMO-K12-%'
               ORDER BY admission_number""",
            (school_id,),
        ).fetchall()
        for index, student in enumerate(demo_students):
            registration_day = 5 + ((index * 7) % 23)
            registration_month = 1 + ((index * 5) % 8)
            registration_year = 2026
            registered_on = f"{registration_year}-{registration_month:02d}-{registration_day:02d}"
            connection.execute(
                """UPDATE students SET enrollment_date=COALESCE(enrollment_date, ?),
                   created_at=CASE WHEN admission_number LIKE 'DEMO-K12-%' THEN ? ELSE created_at END
                   WHERE id=? AND school_id=?""",
                (registered_on, registered_on + " 09:00:00", student["id"], school_id),
            )

        if updater:
            academic_year = "2026-2027"
            finance_count = connection.execute(
                """SELECT COUNT(*) FROM student_finances
                   WHERE school_id=? AND academic_year=? AND student_id IN
                     (SELECT id FROM students WHERE school_id=? AND admission_number LIKE 'DEMO-K12-%')""",
                (school_id, academic_year, school_id),
            ).fetchone()[0]
            if finance_count not in (0, 250):
                raise RuntimeError(
                    f"Found {finance_count} demo financial records; refusing a partial seed."
                )
            if finance_count == 0:
                finance_rows = []
                for index, student in enumerate(demo_students):
                    if index % 5 == 0:
                        amount_paid, note = 8000.00, "FICTIONAL DEMO DATA — shown as paid; not a real receipt."
                    elif index % 5 in (1, 2):
                        amount_paid = (1500.00, 3500.00, 5000.00, 6500.00)[index % 4]
                        note = "FICTIONAL DEMO DATA — partial payment example; not a real receipt."
                    else:
                        amount_paid, note = 0.00, "FICTIONAL DEMO DATA — unpaid example; not a real account statement."
                    finance_rows.append(
                        (student["id"], academic_year, 8000.00, amount_paid, note,
                         updater["id"], school_id)
                    )
                connection.executemany(
                    """INSERT INTO student_finances
                       (student_id, academic_year, total_fee, amount_paid, notes, updated_by, school_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    finance_rows,
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


seed_demo_students_if_enabled()


def write_audit(actor_user_id, action, target_username=None, details=None):
    connection = get_db_connection()
    try:
        connection.execute(
            """
            INSERT INTO audit_log (actor_user_id, action, target_username, details, school_id)
            VALUES (?, ?, ?, ?, (SELECT school_id FROM users WHERE id = ?))
            """,
            (actor_user_id, action, target_username, details, actor_user_id),
        )
        connection.commit()
    finally:
        connection.close()


# --------------------------------------------------
# 3. USER MODEL AND DATABASE LOOKUPS
# --------------------------------------------------

def require_school_context():
    """Return an active school for the authenticated user or fail closed.

    A missing tenant assignment must never silently grant access to the
    legacy/default school's records. Re-check school status on each protected
    request so suspending a school also blocks sessions that are already open.
    Platform administrators need an explicit school assignment before using
    tenant-scoped routes.
    """
    school_id = getattr(current_user, "school_id", None)
    if not current_user.is_authenticated:
        abort(403)

    # Platform super-admins have no permanent tenant assignment. They must
    # explicitly select an active school; the selection is session-scoped and
    # revalidated against the database on every protected request.
    if (
        current_user.role == "super_admin"
        and current_user.is_super_admin
        and school_id is None
    ):
        selected_school_id = session.get("active_school_id")
        if not isinstance(selected_school_id, int) or isinstance(selected_school_id, bool):
            abort(403)
        school_id = selected_school_id
    if school_id is None:
        abort(403)

    connection = get_db_connection()
    try:
        school = connection.execute(
            "SELECT status FROM schools WHERE id = ?", (school_id,)
        ).fetchone()
    finally:
        connection.close()

    if school is None or school["status"] != "active":
        abort(403)
    return school_id


class User(UserMixin):
    def __init__(self, user_id, username, password_hash, role, is_super_admin=False, school_id=None):
        self.id = str(user_id)
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.is_super_admin = bool(is_super_admin)
        # Server-derived tenant context; never trust a school ID from a form/query.
        self.school_id = school_id


def get_user_by_id(user_id):
    connection = get_db_connection()
    try:
        row = connection.execute(
            """
            SELECT u.id, u.username, u.password_hash, u.role, u.is_super_admin, u.school_id
            FROM users u
            WHERE u.id = ? AND u.status = 'active'
              AND (
                  (u.role = 'super_admin' AND u.is_super_admin = 1 AND u.school_id IS NULL)
                  OR (
                  u.role != 'super_admin' AND u.is_super_admin = 0 AND EXISTS (
                      SELECT 1 FROM schools s
                      WHERE s.id = u.school_id AND s.status = 'active'
                  )
              )
              )
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return User(row["id"], row["username"], row["password_hash"],
                    row["role"], row["is_super_admin"], row["school_id"])
    finally:
        connection.close()


def get_user_by_username(username):
    connection = get_db_connection()
    try:
        row = connection.execute(
            """
            SELECT u.id, u.username, u.password_hash, u.role, u.is_super_admin, u.school_id
            FROM users u
            WHERE u.username = ? AND u.status = 'active'
              AND (
                  (u.role = 'super_admin' AND u.is_super_admin = 1 AND u.school_id IS NULL)
                  OR (
                  u.role != 'super_admin' AND u.is_super_admin = 0 AND EXISTS (
                      SELECT 1 FROM schools s
                      WHERE s.id = u.school_id AND s.status = 'active'
                  )
              )
              )
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None
        return User(row["id"], row["username"], row["password_hash"],
                    row["role"], row["is_super_admin"], row["school_id"])
    finally:
        connection.close()


@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)


def require_super_admin():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    # Require all three platform-admin invariants. A malformed account must
    # never gain global access from a flag alone.
    if (
        current_user.role != "super_admin"
        or not current_user.is_super_admin
        or current_user.school_id is not None
    ):
        abort(403)
    return None


# --------------------------------------------------
# 4. PUBLIC PAGES AND AUTHENTICATION
# --------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def health_check():
    """Provide a minimal, non-sensitive readiness check for the hosting platform."""
    connection = None
    try:
        connection = get_db_connection()
        connection.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok"}), 200
    except sqlite3.Error:
        app.logger.exception("Health check failed while accessing the database.")
        return jsonify({"status": "unavailable"}), 503
    finally:
        if connection is not None:
            connection.close()


@app.route("/features")
def feature_center():
    """Show the current feature map and send users to implemented workflows."""
    return render_template("features.html")


LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60


def login_is_rate_limited(username, now=None):
    """Return whether this username is temporarily locked after failed logins.

    State is stored in SQLite rather than process memory so it is shared by
    workers using the same database. Use a generic login response either way.
    """
    now = time.time() if now is None else now
    key = (username or "").strip().casefold()[:64]
    if not key:
        return False
    connection = get_db_connection()
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS login_attempt_limits (
            username_key TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            window_started REAL NOT NULL,
            locked_until REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )""")
        row = connection.execute(
            "SELECT locked_until FROM login_attempt_limits WHERE username_key=?", (key,)
        ).fetchone()
        return bool(row and row["locked_until"] > now)
    finally:
        connection.close()


def record_failed_login(username, now=None):
    """Count failed logins and apply a 15-minute lock after five in 15 minutes."""
    now = time.time() if now is None else now
    key = (username or "").strip().casefold()[:64]
    if not key:
        return
    connection = get_db_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE IF NOT EXISTS login_attempt_limits (
            username_key TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            window_started REAL NOT NULL,
            locked_until REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )""")
        # Keep this small operational table bounded against random-username abuse.
        connection.execute("DELETE FROM login_attempt_limits WHERE updated_at < ?", (now - 86400,))
        row = connection.execute(
            "SELECT failed_attempts,window_started,locked_until FROM login_attempt_limits WHERE username_key=?",
            (key,),
        ).fetchone()
        if row and row["locked_until"] > now:
            connection.commit()
            return
        if not row or now - row["window_started"] >= LOGIN_FAILURE_WINDOW_SECONDS:
            attempts, started = 1, now
        else:
            attempts, started = row["failed_attempts"] + 1, row["window_started"]
        locked_until = now + LOGIN_LOCKOUT_SECONDS if attempts >= LOGIN_FAILURE_LIMIT else 0
        connection.execute("""INSERT INTO login_attempt_limits
            (username_key,failed_attempts,window_started,locked_until,updated_at)
            VALUES (?,?,?,?,?) ON CONFLICT(username_key) DO UPDATE SET
            failed_attempts=excluded.failed_attempts,window_started=excluded.window_started,
            locked_until=excluded.locked_until,updated_at=excluded.updated_at""",
            (key, attempts, started, locked_until, now))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def clear_failed_logins(username):
    key = (username or "").strip().casefold()[:64]
    if not key:
        return
    connection = get_db_connection()
    try:
        connection.execute("DELETE FROM login_attempt_limits WHERE username_key=?", (key,))
        connection.commit()
    finally:
        connection.close()

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password or len(username) > 64 or len(password) > 1024:
            flash("Invalid username or password.")
            return render_template("login.html")

        if login_is_rate_limited(username):
            # Do not reveal whether the account exists or is temporarily locked.
            flash("Invalid username or password. Please wait before trying again.")
            return render_template("login.html"), 429

        user = get_user_by_username(username)
        if user and check_password_hash(user.password_hash, password):
            clear_failed_logins(username)
            session.clear()
            session.permanent = True
            login_user(user)
            write_audit(user.id, "login_success", user.username)
            return redirect(url_for("dashboard"))
        record_failed_login(username)
        write_audit(None, "login_failed", username[:64] or None)
        flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    school_context_selected = not current_user.is_super_admin
    if current_user.is_super_admin:
        selected_school_id = session.get("active_school_id")
        if isinstance(selected_school_id, int) and not isinstance(selected_school_id, bool):
            connection = get_db_connection()
            try:
                school_context_selected = connection.execute(
                    "SELECT 1 FROM schools WHERE id = ? AND status = 'active'",
                    (selected_school_id,),
                ).fetchone() is not None
            finally:
                connection.close()
    return render_template(
        "dashboard.html",
        name=current_user.username,
        role=current_user.role,
        school_context_selected=school_context_selected,
    )


@app.route("/admin/select-school", methods=["GET", "POST"])
@login_required
def select_school_context():
    """Let platform super-admins explicitly enter an active school's workspace."""
    if (
        current_user.role != "super_admin"
        or not current_user.is_super_admin
        or current_user.school_id is not None
    ):
        abort(403)

    connection = get_db_connection()
    try:
        schools = connection.execute(
            "SELECT id, name, slug FROM schools WHERE status='active' ORDER BY name COLLATE NOCASE"
        ).fetchall()
        if request.method == "POST":
            selected = request.form.get("school_id", "").strip()
            if not selected.isdigit():
                flash("Choose an active school.", "danger")
                return render_template(
                    "select_school.html", schools=schools,
                    active_school_id=session.get("active_school_id")
                )
            school = connection.execute(
                "SELECT id FROM schools WHERE id=? AND status='active'", (int(selected),)
            ).fetchone()
            if school is None:
                flash("That school is not active. Choose an active school.", "danger")
                return render_template(
                    "select_school.html", schools=schools,
                    active_school_id=session.get("active_school_id")
                )
            session["active_school_id"] = int(school["id"])
            flash("School workspace selected. School records remain scoped to this school.", "success")
            return redirect(url_for("dashboard"))
        return render_template(
            "select_school.html", schools=schools,
            active_school_id=session.get("active_school_id")
        )
    finally:
        connection.close()


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    write_audit(current_user.id, "logout", current_user.username)
    session.pop("active_school_id", None)
    logout_user()
    return redirect(url_for("index"))


# --------------------------------------------------
# 5. ADMINISTRATOR REQUEST AND MANAGEMENT
# --------------------------------------------------

@app.route("/admin/request-access", methods=["GET", "POST"])
def request_staff_access():
    """Collect a school-specific staff request; requests never grant login access."""
    connection = get_db_connection()
    try:
        schools = connection.execute(
            "SELECT id, name, slug FROM schools WHERE status = 'active' ORDER BY name COLLATE NOCASE"
        ).fetchall()
    finally:
        connection.close()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        requested_role = request.form.get("requested_role", "").strip()
        school_slug = request.form.get("school_slug", "").strip()

        selected_school = next((school for school in schools if school["slug"] == school_slug), None)
        if not selected_school:
            flash("Choose an active school from the list.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES, schools=schools)

        if not full_name or not username or requested_role not in STAFF_ROLES:
            flash("Complete all fields and choose a valid staff role.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES, schools=schools)

        if len(full_name) > 120 or len(username) > 64:
            flash("Name or username is too long.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES, schools=schools)
        if not username.replace("_", "").replace("-", "").isalnum():
            flash("Use only letters, numbers, underscores or hyphens in the username.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES, schools=schools)

        # The server resolves the submitted slug against active schools. The client
        # never supplies a school ID, and requests remain pending until approval.
        placeholder_password = generate_password_hash(secrets.token_urlsafe(48))
        connection = get_db_connection()
        try:
            connection.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role, status, is_super_admin, school_id)
                VALUES (?, ?, ?, ?, 'pending', 0, ?)
                """,
                (username, full_name, placeholder_password, requested_role, selected_school["id"]),
            )
            connection.commit()
            flash("Access request submitted. You cannot sign in until it is approved.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already in use or has a request pending.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES, schools=schools)
        finally:
            connection.close()

    return render_template("admin_request.html", roles=STAFF_ROLES, schools=schools)


@app.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_users():
    denial = require_super_admin()
    if denial:
        return denial

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        target_id = request.form.get("user_id", "").strip()
        connection = get_db_connection()
        try:
            # Account creation is handled before loading an existing target account.
            if action == "create_account":
                username = request.form.get("new_username", "").strip()
                full_name = request.form.get("new_full_name", "").strip()
                role = request.form.get("new_role", "").strip()
                password = request.form.get("new_password", "")
                school_text = request.form.get("school_id", "").strip()
                linked_student_text = request.form.get("linked_student_id", "").strip()
                if (not username or len(username) > 64
                    or not username.replace("_", "").replace("-", "").isalnum()
                    or not full_name or len(full_name) > 120
                    or role not in {"student", "parent"}
                    or len(password) < 12
                    or not school_text.isdigit()):
                    flash("Enter a valid name, username, school, role, and temporary password of at least 12 characters.", "danger")
                    return redirect(url_for("admin_users"))
                school = connection.execute(
                    "SELECT id FROM schools WHERE id = ? AND status = 'active'", (int(school_text),)
                ).fetchone()
                if not school:
                    flash("Choose an active school.", "danger")
                    return redirect(url_for("admin_users"))
                student = None
                if role in {"student", "parent"}:
                    if not linked_student_text.isdigit():
                        flash("Select the verified student record for this account.", "danger")
                        return redirect(url_for("admin_users"))
                    student = connection.execute(
                        "SELECT id, account_user_id FROM students WHERE id = ? AND school_id = ? AND status = 'active'",
                        (int(linked_student_text), int(school_text)),
                    ).fetchone()
                    if not student:
                        flash("The selected student is not active in that school.", "danger")
                        return redirect(url_for("admin_users"))
                if role == "parent" and request.form.get("verify_identity") != "yes":
                    flash("Confirm that the guardian's identity and relationship to the student have been verified against school records.", "danger")
                    return redirect(url_for("admin_users"))
                if role == "student" and student and student["account_user_id"] is not None:
                    flash("That student already has a login account.", "danger")
                    return redirect(url_for("admin_users"))
                try:
                    cursor = connection.execute(
                        """INSERT INTO users
                           (username, full_name, password_hash, role, status, is_super_admin, school_id,
                            approved_by, approved_at)
                           VALUES (?, ?, ?, ?, 'active', 0, ?, ?, CURRENT_TIMESTAMP)""",
                        (username, full_name, generate_password_hash(password), role, int(school_text), current_user.id),
                    )
                    new_user_id = cursor.lastrowid
                    if role == "student":
                        connection.execute(
                            "UPDATE students SET account_user_id = ? WHERE id = ? AND school_id = ? AND account_user_id IS NULL",
                            (new_user_id, student["id"], int(school_text)),
                        )
                        audit_action = "student_account_created"
                    else:
                        connection.execute(
                            """INSERT INTO parent_student_links
                               (parent_user_id, student_id, school_id, created_by)
                               VALUES (?, ?, ?, ?)""",
                            (new_user_id, student["id"], int(school_text), current_user.id),
                        )
                        audit_action = "parent_account_created_and_linked"
                    connection.execute(
                        """INSERT INTO audit_log
                           (actor_user_id, action, target_username, details, school_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (current_user.id, audit_action, username, "Role: " + role, int(school_text)),
                    )
                    connection.commit()
                    flash("Account created and linked to the verified student record. Give the temporary password privately and require the user to change it.", "success")
                except sqlite3.IntegrityError:
                    connection.rollback()
                    flash("That username already exists, the student is already linked, or the account could not be created.", "danger")
                return redirect(url_for("admin_users"))

            target = connection.execute(
                "SELECT id, username, role, status, is_super_admin, school_id FROM users WHERE id = ?",
                (target_id,),
            ).fetchone()
            if target is None:
                flash("The selected account could not be found.", "danger")
                return redirect(url_for("admin_users"))

            target_id_int = target["id"]
            target_username = target["username"]

            if action == "approve":
                temporary_password = request.form.get("temporary_password", "")
                if target["status"] != "pending":
                    flash("Only pending requests can be approved.", "danger")
                elif len(temporary_password) < 12:
                    flash("Use a temporary password with at least 12 characters.", "danger")
                else:
                    connection.execute(
                        """
                        UPDATE users SET password_hash = ?, status = 'active',
                            approved_by = ?, approved_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND status = 'pending'
                        """,
                        (generate_password_hash(temporary_password), current_user.id, target_id_int),
                    )
                    connection.execute(
                        """
                        INSERT INTO audit_log (actor_user_id, action, target_username, details, school_id)
                        VALUES (?, 'staff_request_approved', ?, ?, ?)
                        """,
                        (current_user.id, target_username, "Role: " + target["role"], target["school_id"]),
                    )
                    connection.commit()
                    flash("Account approved and activated. Share the temporary password securely.", "success")
            elif action == "unlink_parent":
                if target["role"] != "parent":
                    flash("Only parent accounts can be unlinked from a student.", "danger")
                else:
                    student_id = request.form.get("student_id", "").strip()
                    deleted = connection.execute(
                        """DELETE FROM parent_student_links
                           WHERE parent_user_id = ? AND student_id = ? AND school_id = ?""",
                        (target_id_int, student_id, target["school_id"]),
                    ).rowcount
                    if deleted:
                        connection.execute(
                            """INSERT INTO audit_log
                               (actor_user_id, action, target_username, details, school_id)
                               VALUES (?, 'parent_student_unlinked', ?, ?, ?)""",
                            (current_user.id, target_username, "Student record ID: " + student_id, target["school_id"]),
                        )
                        connection.commit()
                        flash("Parent account unlinked from that student. The student record remains unchanged.", "success")
                    else:
                        flash("No matching parent-child link was found in this school.", "info")
            elif action == "delete_account":
                if target["is_super_admin"] or target_id_int == current_user.id:
                    flash("You cannot delete your own account or a Super Administrator account here.", "danger")
                else:
                    # Safe deletion means removing login access and relationships while
                    # retaining the user row for audit/financial foreign-key integrity.
                    connection.execute(
                        "UPDATE students SET account_user_id = NULL WHERE account_user_id = ? AND school_id = ?",
                        (target_id_int, target["school_id"]),
                    )
                    connection.execute(
                        "DELETE FROM parent_student_links WHERE parent_user_id = ? AND school_id = ?",
                        (target_id_int, target["school_id"]),
                    )
                    tombstone = "deactivated-" + str(target_id_int) + "-" + secrets.token_hex(4)
                    connection.execute(
                        "UPDATE users SET username = ?, full_name = 'Deactivated account', password_hash = ?, status = 'suspended' WHERE id = ? AND is_super_admin = 0",
                        (tombstone, generate_password_hash(secrets.token_urlsafe(48)), target_id_int),
                    )
                    connection.execute(
                        """INSERT INTO audit_log
                           (actor_user_id, action, target_username, details, school_id)
                           VALUES (?, 'account_deactivated', ?, ?, ?)""",
                        (current_user.id, target_username, "Login disabled; school records and audit references retained.", target["school_id"]),
                    )
                    connection.commit()
                    flash("Account access removed. The login is disabled and linked school records are preserved for audit and retention.", "success")
            elif action in ("suspend", "reactivate"):
                if target["is_super_admin"]:
                    flash("Super Administrator accounts cannot be suspended or reactivated from this page.", "danger")
                    return redirect(url_for("admin_users"))
                new_status = "suspended" if action == "suspend" else "active"
                connection.execute(
                    "UPDATE users SET status = ? WHERE id = ?",
                    (new_status, target_id_int),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log (actor_user_id, action, target_username, details, school_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (current_user.id, "staff_account_" + action, target_username,
                     "Status changed to " + new_status, target["school_id"]),
                )
                connection.commit()
                flash("Account status updated.", "success")
            elif action == "link_student":
                student_id = request.form.get("student_id", "").strip()
                if target["role"] != "student" or target["status"] != "active":
                    flash("Only an active student account can be linked to a student record.", "danger")
                elif request.form.get("verify_identity") != "yes":
                    flash("Confirm that the student's identity and admission number have been checked before linking.", "danger")
                else:
                    student = connection.execute(
                        "SELECT id, admission_number FROM students WHERE id = ? AND school_id = ? AND status = 'active'",
                        (student_id, target["school_id"]),
                    ).fetchone()
                    if not student:
                        flash("Choose a valid active student record.", "danger")
                    else:
                        # Never clear an account's existing link globally: a stale or
                        # corrupt cross-school link must be reviewed, not silently moved.
                        existing_link = connection.execute(
                            "SELECT id, school_id, admission_number FROM students WHERE account_user_id = ?",
                            (target_id_int,),
                        ).fetchone()
                        selected_record = connection.execute(
                            "SELECT account_user_id FROM students WHERE id = ? AND school_id = ? AND status = 'active'",
                            (student["id"], target["school_id"]),
                        ).fetchone()
                        if existing_link and (
                            existing_link["id"] != student["id"]
                            or existing_link["school_id"] != target["school_id"]
                        ):
                            flash("This account is already linked to a different student record. Resolve the existing link before changing it.", "danger")
                        elif not selected_record or selected_record["account_user_id"] not in (None, target_id_int):
                            flash("That student record is already linked to another account.", "danger")
                        else:
                            connection.execute(
                                "UPDATE students SET account_user_id = ? WHERE id = ? AND school_id = ? AND status = 'active'",
                                (target_id_int, student["id"], target["school_id"]),
                            )
                            connection.execute(
                                "INSERT INTO audit_log (actor_user_id, action, target_username, details, school_id) VALUES (?, 'student_account_linked', ?, ?, ?)",
                                (current_user.id, target_username, "Student record: " + student["admission_number"], target["school_id"]),
                            )
                            connection.commit()
                            flash("Student account linked to the verified student record.", "success")
            elif action == "unlink_student":
                linked = connection.execute(
                    "SELECT admission_number FROM students WHERE account_user_id = ? AND school_id = ?",
                    (target_id_int, target["school_id"]),
                ).fetchone()
                if target["role"] != "student":
                    flash("Only student accounts can be unlinked from student records.", "danger")
                elif linked:
                    connection.execute(
                        "UPDATE students SET account_user_id = NULL WHERE account_user_id = ? AND school_id = ?",
                        (target_id_int, target["school_id"]),
                    )
                    connection.execute(
                        "INSERT INTO audit_log (actor_user_id, action, target_username, details, school_id) VALUES (?, 'student_account_unlinked', ?, ?, ?)",
                        (current_user.id, target_username, "Student record: " + linked["admission_number"], target["school_id"]),
                    )
                    connection.commit()
                    flash("Student account unlinked.", "success")
                else:
                    flash("This account has no linked student record.", "info")
            elif action == "change_role":
                new_role = request.form.get("new_role", "").strip()
                linked = connection.execute("SELECT 1 FROM students WHERE account_user_id = ?", (target_id_int,)).fetchone()
                if target["is_super_admin"] or new_role not in STAFF_ROLES:
                    flash("Super Administrator roles cannot be changed here.", "danger")
                elif linked:
                    flash("Unlink this student record before changing the account's role.", "danger")
                else:
                    connection.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, target_id_int))
                    connection.execute(
                        "INSERT INTO audit_log (actor_user_id, action, target_username, details, school_id) VALUES (?, 'staff_role_changed', ?, ?, ?)",
                        (current_user.id, target_username, "New role: " + new_role, target["school_id"]),
                    )
                    connection.commit()
                    flash("Staff role updated.", "success")
            else:
                flash("Unknown account-management action.", "danger")
        finally:
            connection.close()
        return redirect(url_for("admin_users"))

    connection = get_db_connection()
    try:
        users = connection.execute(
            """
            SELECT u.id, u.username, u.full_name, u.role, u.status, u.is_super_admin,
                   u.created_at, u.approved_at, s.id AS linked_student_id,
                   s.full_name AS linked_student_name, s.admission_number AS linked_admission_number
            FROM users u LEFT JOIN students s
                ON s.account_user_id = u.id AND s.school_id = u.school_id
            ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                     u.username COLLATE NOCASE
            """
        ).fetchall()
        parent_links = connection.execute(
            """SELECT l.parent_user_id, l.student_id, l.school_id, s.full_name AS student_name,
                      s.admission_number, u.username AS parent_username
               FROM parent_student_links l
               JOIN users u ON u.id = l.parent_user_id AND u.school_id = l.school_id
               JOIN students s ON s.id = l.student_id AND s.school_id = l.school_id
               ORDER BY u.username COLLATE NOCASE, s.full_name COLLATE NOCASE"""
        ).fetchall()
        schools = connection.execute(
            "SELECT id, name FROM schools WHERE status = 'active' ORDER BY name COLLATE NOCASE"
        ).fetchall()
        available_students = connection.execute(
            """SELECT id, full_name, admission_number, class_level, account_user_id
               FROM students WHERE status = 'active' ORDER BY class_level, full_name"""
        ).fetchall()
        audit_entries = connection.execute(
            """
            SELECT audit_log.action, audit_log.target_username, audit_log.details,
                   audit_log.created_at, users.username AS actor_username
            FROM audit_log LEFT JOIN users ON users.id = audit_log.actor_user_id
            ORDER BY audit_log.id DESC LIMIT 20
            """
        ).fetchall()
    finally:
        connection.close()

    return render_template(
        "admin_users.html", users=users, roles=STAFF_ROLES,
        available_students=available_students, audit_entries=audit_entries,
        parent_links=parent_links, schools=schools,
    )


# --------------------------------------------------
# 6. GRADES AND ATTENDANCE
# --------------------------------------------------

ACADEMIC_ENTRY_ROLES = {"super_admin", "teacher", "academic_admin", "vice_principal", "attendance_officer"}
ACADEMIC_VIEW_ROLES = {
    "super_admin", "school_admin", "teacher", "academic_admin", "principal",
    "vice_principal", "registrar", "attendance_officer",
}


@app.route("/academics", methods=["GET", "POST"])
@login_required
def academics():
    """Record and review demo grades and daily attendance with role checks."""
    if current_user.role not in ACADEMIC_VIEW_ROLES:
        abort(403)
    school_id = require_school_context()

    connection = get_db_connection()
    try:
        students = connection.execute(
            "SELECT id, admission_number, full_name, class_level FROM students WHERE school_id = ? AND status = 'active' ORDER BY class_level, full_name",
            (school_id,),
        ).fetchall()
        if request.method == "POST":
            if current_user.role not in ACADEMIC_ENTRY_ROLES:
                abort(403)
            action = request.form.get("action", "").strip()
            # Attendance officers may record attendance only, never assessment grades.
            if current_user.role == "attendance_officer" and action != "attendance":
                abort(403)
            student_id = request.form.get("student_id", "").strip()
            student = connection.execute(
                "SELECT id FROM students WHERE id = ? AND school_id = ? AND status = 'active'",
                (student_id, school_id),
            ).fetchone()
            if not student:
                flash("Choose a valid active student.", "danger")
                return redirect(url_for("academics"))

            if action == "grade":
                subject = request.form.get("subject", "").strip()
                assessment = request.form.get("assessment", "").strip()
                academic_year = request.form.get("academic_year", "").strip()
                term = request.form.get("term", "").strip()
                score_text = request.form.get("score", "").strip()
                max_text = request.form.get("max_score", "").strip()
                if not subject or len(subject) > 80 or not assessment or len(assessment) > 100:
                    flash("Enter a subject and assessment name within the stated limits.", "danger")
                    return redirect(url_for("academics"))
                if (len(academic_year) != 9 or academic_year[4] != "-"
                        or not academic_year[:4].isdigit() or not academic_year[5:].isdigit()
                        or int(academic_year[5:]) != int(academic_year[:4]) + 1
                        or term not in {"Term 1", "Term 2", "Term 3"}):
                    flash("Choose a valid academic year (for example 2026-2027) and term.", "danger")
                    return redirect(url_for("academics"))
                try:
                    score, max_score = float(score_text), float(max_text)
                    if not (0 <= score <= 100 and 0 < max_score <= 100 and score <= max_score):
                        raise ValueError
                except (ValueError, TypeError):
                    flash("Scores must be valid numbers: score 0–100, maximum 1–100, and score cannot exceed maximum.", "danger")
                    return redirect(url_for("academics"))
                published_report = connection.execute(
                    """SELECT 1 FROM report_cards
                       WHERE student_id = ? AND school_id = ? AND academic_year = ? AND term = ? AND published = 1""",
                    (student_id, require_school_context(), academic_year, term),
                ).fetchone()
                if published_report:
                    flash("This term's report card is published. Unpublish it before recording additional grades.", "danger")
                    return redirect(url_for("academics"))
                try:
                    connection.execute(
                        """INSERT INTO grades
                           (student_id, subject, assessment, score, max_score, recorded_by, academic_year, term, school_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (student_id, subject, assessment, score, max_score, current_user.id, academic_year, term, require_school_context()),
                    )
                    connection.commit()
                    write_audit(current_user.id, "grade_recorded", str(student_id),
                                f"{subject} / {assessment}: {score}/{max_score}")
                    flash("Grade saved successfully.", "success")
                except sqlite3.IntegrityError:
                    flash("This student already has that assessment in the selected subject, academic year, and term.", "danger")
            elif action == "attendance":
                attendance_date = request.form.get("attendance_date", "").strip()
                status = request.form.get("status", "").strip()
                note = request.form.get("note", "").strip()
                try:
                    datetime.strptime(attendance_date, "%Y-%m-%d")
                    if status not in {"present", "absent", "late", "excused"} or len(note) > 250:
                        raise ValueError
                except (ValueError, TypeError):
                    flash("Choose a valid date and attendance status. Notes must be 250 characters or fewer.", "danger")
                    return redirect(url_for("academics"))
                try:
                    connection.execute(
                        """INSERT INTO attendance (student_id, attendance_date, status, note, recorded_by, school_id)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (student_id, attendance_date, status, note or None, current_user.id, require_school_context()),
                    )
                    connection.commit()
                    write_audit(current_user.id, "attendance_recorded", str(student_id),
                                f"{attendance_date}: {status}")
                    flash("Attendance saved successfully.", "success")
                except sqlite3.IntegrityError:
                    flash("Attendance has already been recorded for this student on that date.", "danger")
            else:
                flash("Unknown academic action.", "danger")
            return redirect(url_for("academics"))

        grades = connection.execute(
            """SELECT g.id, s.full_name, s.admission_number, s.class_level, g.subject,
                      g.assessment, g.score, g.max_score, g.academic_year, g.term,
                      u.username AS recorded_by, g.created_at
               FROM grades g JOIN students s ON s.id = g.student_id
               JOIN users u ON u.id = g.recorded_by AND u.school_id = g.school_id
               WHERE g.school_id = ? AND s.school_id = ?
               ORDER BY g.id DESC LIMIT 100""",
            (require_school_context(), require_school_context()),
        ).fetchall()
        attendance_rows = connection.execute(
            """SELECT a.id, s.full_name, s.admission_number, s.class_level,
                      a.attendance_date, a.status, a.note, u.username AS recorded_by
               FROM attendance a JOIN students s ON s.id = a.student_id
               JOIN users u ON u.id = a.recorded_by AND u.school_id = a.school_id
               WHERE a.school_id = ? AND s.school_id = ?
               ORDER BY a.attendance_date DESC, a.id DESC LIMIT 100""",
            (require_school_context(), require_school_context()),
        ).fetchall()
    finally:
        connection.close()
    return render_template(
        "academics.html", students=students, grades=grades,
        attendance_rows=attendance_rows,
        can_enter=current_user.role in ACADEMIC_ENTRY_ROLES,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


# --------------------------------------------------
# 6. CLASS TIMETABLE
# --------------------------------------------------

TIMETABLE_VIEW_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal",
    "academic_admin", "registrar", "teacher",
}
TIMETABLE_MANAGE_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal",
    "academic_admin",
}
TIMETABLE_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
TIMETABLE_CLASS_LEVELS = ("K2", "K3", "K4", *(f"Grade {i}" for i in range(1, 13)))


@app.route("/timetable", methods=["GET", "POST"])
@login_required
def timetable():
    """View and manage a school's timetable without crossing tenant boundaries."""
    if current_user.role not in TIMETABLE_VIEW_ROLES:
        abort(403)
    school_id = require_school_context()
    can_manage = current_user.role in TIMETABLE_MANAGE_ROLES
    connection = get_db_connection()
    try:
        if request.method == "POST":
            if not can_manage:
                abort(403)
            action = request.form.get("action", "create").strip()
            if action == "delete":
                schedule_id = request.form.get("schedule_id", "").strip()
                if not schedule_id.isdigit():
                    flash("Choose a valid timetable entry to remove.", "danger")
                    return redirect(url_for("timetable"))
                cursor = connection.execute(
                    "DELETE FROM class_timetable WHERE id = ? AND school_id = ?",
                    (int(schedule_id), school_id),
                )
                connection.commit()
                if cursor.rowcount:
                    write_audit(current_user.id, "timetable_entry_deleted", details=f"entry_id={int(schedule_id)}")
                    flash("Timetable entry removed.", "success")
                else:
                    flash("That timetable entry was not found in this school.", "danger")
                return redirect(url_for("timetable"))

            class_level = request.form.get("class_level", "").strip()
            subject = request.form.get("subject", "").strip()
            teacher_text = request.form.get("teacher_id", "").strip()
            weekday = request.form.get("weekday", "").strip()
            start_time = request.form.get("start_time", "").strip()
            end_time = request.form.get("end_time", "").strip()
            room = request.form.get("room", "").strip()

            def valid_time(value):
                return (
                    len(value) == 5 and value[2] == ":" and value[:2].isdigit()
                    and value[3:].isdigit() and 0 <= int(value[:2]) <= 23
                    and 0 <= int(value[3:]) <= 59
                )

            if (
                class_level not in TIMETABLE_CLASS_LEVELS
                or not subject or len(subject) > 80
                or not teacher_text.isdigit()
                or weekday not in TIMETABLE_DAYS
                or not valid_time(start_time) or not valid_time(end_time)
                or start_time >= end_time or len(room) > 80
            ):
                flash("Check the class, subject, teacher, day, times and room. End time must be after start time.", "danger")
                return redirect(url_for("timetable"))

            teacher = connection.execute(
                """SELECT id FROM users
                   WHERE id = ? AND school_id = ? AND role = 'teacher' AND status = 'active'""",
                (int(teacher_text), school_id),
            ).fetchone()
            if teacher is None:
                flash("Choose an active teacher assigned to this school.", "danger")
                return redirect(url_for("timetable"))

            conflict = connection.execute(
                """SELECT id FROM class_timetable
                   WHERE school_id = ? AND weekday = ?
                     AND start_time < ? AND end_time > ?
                     AND (class_level = ? OR teacher_id = ?
                          OR (? <> '' AND room <> '' AND lower(room) = lower(?)))
                   LIMIT 1""",
                (school_id, weekday, end_time, start_time, class_level,
                 int(teacher_text), room, room),
            ).fetchone()
            if conflict:
                flash("Schedule conflict: this class, teacher or room already has an overlapping lesson.", "danger")
                return redirect(url_for("timetable"))

            connection.execute(
                """INSERT INTO class_timetable
                   (school_id, class_level, subject, teacher_id, weekday,
                    start_time, end_time, room, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (school_id, class_level, subject, int(teacher_text), weekday,
                 start_time, end_time, room, int(current_user.id)),
            )
            connection.commit()
            write_audit(
                current_user.id, "timetable_entry_created",
                details=f"class={class_level}; day={weekday}; time={start_time}-{end_time}",
            )
            flash("Class timetable entry saved.", "success")
            return redirect(url_for("timetable"))

        entries = connection.execute(
            """SELECT t.id, t.class_level, t.subject, t.weekday, t.start_time,
                      t.end_time, t.room, u.full_name AS teacher_name, u.username AS teacher_username
               FROM class_timetable t
               JOIN users u ON u.id = t.teacher_id AND u.school_id = t.school_id
               WHERE t.school_id = ?
               ORDER BY CASE t.weekday
                    WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END,
                    t.start_time, t.class_level""",
            (school_id,),
        ).fetchall()
        teachers = []
        if can_manage:
            teachers = connection.execute(
                """SELECT id, username, full_name FROM users
                   WHERE school_id = ? AND role = 'teacher' AND status = 'active'
                   ORDER BY full_name COLLATE NOCASE, username COLLATE NOCASE""",
                (school_id,),
            ).fetchall()
    finally:
        connection.close()
    return render_template(
        "timetable.html", entries=entries, teachers=teachers, can_manage=can_manage,
        days=TIMETABLE_DAYS, class_levels=TIMETABLE_CLASS_LEVELS,
    )


# --------------------------------------------------
# 7. REPORT CARDS
# --------------------------------------------------

REPORT_CARD_VIEW_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal",
    "academic_admin", "teacher", "registrar",
}
REPORT_CARD_PUBLISH_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal", "academic_admin",
}


@app.route("/my-report-cards")
@login_required
def my_report_cards():
    """Students can see only published reports for their own linked record."""
    if current_user.role != "student":
        abort(403)
    connection = get_db_connection()
    try:
        student = connection.execute(
            """SELECT id, admission_number, full_name, class_level
               FROM students WHERE account_user_id = ? AND school_id = ? AND status = 'active'""",
            (current_user.id, require_school_context()),
        ).fetchone()
        reports = []
        if student:
            reports = connection.execute(
                """SELECT rc.academic_year, rc.term, rc.published_at,
                          g.subject, g.score, g.max_score, g.assessment
                   FROM report_cards rc
                   JOIN grades g ON g.student_id = rc.student_id
                       AND g.academic_year = rc.academic_year AND g.term = rc.term
                       AND g.school_id = rc.school_id
                   WHERE rc.student_id = ? AND rc.school_id = ? AND g.school_id = ? AND rc.published = 1
                   ORDER BY rc.academic_year DESC, rc.term, g.subject COLLATE NOCASE,
                            g.assessment COLLATE NOCASE""",
                (student["id"], require_school_context(), require_school_context()),
            ).fetchall()
    finally:
        connection.close()
    return render_template("my_report_cards.html", student=student, reports=reports)


@app.route("/report-cards", methods=["GET", "POST"])
@login_required
def report_cards():
    """Build subject summaries and control publication for a selected student and term."""
    if current_user.role not in REPORT_CARD_VIEW_ROLES:        abort(403)

    connection = get_db_connection()
    try:
        students = connection.execute(
            "SELECT id, admission_number, full_name, class_level FROM students WHERE school_id = ? AND status = 'active' ORDER BY class_level, full_name",
            (require_school_context(),),
        ).fetchall()
        student_id = request.values.get("student_id", "").strip()
        academic_year = request.values.get("academic_year", "").strip()
        term = request.values.get("term", "").strip()
        selected_student = None
        subject_rows = []
        publication = None

        if student_id and academic_year and term in {"Term 1", "Term 2", "Term 3"}:
            selected_student = connection.execute(
                "SELECT id, admission_number, full_name, class_level FROM students WHERE id = ? AND school_id = ? AND status = 'active'",
                (student_id, require_school_context()),
            ).fetchone()

        if request.method == "POST":
            if current_user.role not in REPORT_CARD_PUBLISH_ROLES:
                abort(403)
            action = request.form.get("action", "").strip()
            year_valid = (len(academic_year) == 9 and academic_year[4] == "-"
                          and academic_year[:4].isdigit() and academic_year[5:].isdigit()
                          and int(academic_year[5:]) == int(academic_year[:4]) + 1)
            if not selected_student or not year_valid or term not in {"Term 1", "Term 2", "Term 3"}:
                flash("Select a valid student, academic year, and term.", "danger")
                return redirect(url_for("report_cards"))
            has_grades = connection.execute(
                "SELECT 1 FROM grades WHERE student_id = ? AND school_id = ? AND academic_year = ? AND term = ? LIMIT 1",
                (student_id, require_school_context(), academic_year, term),
            ).fetchone()
            if not has_grades:
                flash("A report card can only be published after grades have been recorded for that term.", "danger")
                return redirect(url_for("report_cards", student_id=student_id, academic_year=academic_year, term=term))
            if action not in {"publish", "unpublish"}:
                flash("Unknown report-card action.", "danger")
                return redirect(url_for("report_cards"))
            published = 1 if action == "publish" else 0
            connection.execute(
                """INSERT INTO report_cards (student_id, academic_year, term, published, published_by, published_at, school_id)
                   VALUES (?, ?, ?, ?, ?, CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END, ?)
                   ON CONFLICT(student_id, academic_year, term) DO UPDATE SET
                     published = excluded.published, published_by = excluded.published_by,
                     published_at = CASE WHEN excluded.published = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                     updated_at = CURRENT_TIMESTAMP""",
                (student_id, academic_year, term, published, current_user.id, published, require_school_context()),
            )
            connection.commit()
            write_audit(current_user.id, "report_card_" + action, selected_student["admission_number"],
                        academic_year + " / " + term)
            flash("Report card publication updated.", "success")
            return redirect(url_for("report_cards", student_id=student_id, academic_year=academic_year, term=term))

        if selected_student:
            raw_grades = connection.execute(
                """SELECT subject, score, max_score, assessment FROM grades
                   WHERE student_id = ? AND school_id = ? AND academic_year = ? AND term = ?
                   ORDER BY subject COLLATE NOCASE, assessment COLLATE NOCASE""",
                (student_id, require_school_context(), academic_year, term),
            ).fetchall()
            by_subject = {}
            for grade in raw_grades:
                item = by_subject.setdefault(grade["subject"], {"subject": grade["subject"], "earned": 0.0, "possible": 0.0, "assessments": 0})
                item["earned"] += float(grade["score"])
                item["possible"] += float(grade["max_score"])
                item["assessments"] += 1
            subject_rows = [dict(item, percentage=round(item["earned"] / item["possible"] * 100, 1))
                            for item in by_subject.values() if item["possible"] > 0]
            publication = connection.execute(
                "SELECT published, published_at FROM report_cards WHERE student_id = ? AND school_id = ? AND academic_year = ? AND term = ?",
                (student_id, require_school_context(), academic_year, term),
            ).fetchone()
    finally:
        connection.close()

    return render_template(
        "report_cards.html", students=students, selected_student=selected_student,
        academic_year=academic_year, term=term, subject_rows=subject_rows,
        publication=publication, can_publish=current_user.role in REPORT_CARD_PUBLISH_ROLES,
    )


# --------------------------------------------------
# 7. STUDENT RECORDS
# --------------------------------------------------

@app.route("/students/register", methods=["GET", "POST"])
@login_required
def register_student():
    if current_user.role not in ("super_admin", "school_admin", "registrar", "admissions_officer", "teacher"):
        flash("You are not authorized to register students.", "danger")
        return redirect(url_for("dashboard"))
    # Enforce tenant context on GET as well as POST so platform admins cannot
    # open a school-scoped form that they cannot safely submit.
    require_school_context()

    if request.method == "POST":
        admission_number = request.form.get("admission_number", "").strip()
        full_name = request.form.get("full_name", "").strip()
        class_level = request.form.get("class_level", "").strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        guardian_name = request.form.get("guardian_name", "").strip()
        guardian_phone = request.form.get("guardian_phone", "").strip()
        enrollment_date = request.form.get("enrollment_date", "").strip()
        previous_school = request.form.get("previous_school", "").strip()
        emergency_contact_name = request.form.get("emergency_contact_name", "").strip()
        emergency_contact_phone = request.form.get("emergency_contact_phone", "").strip()
        record_notes = request.form.get("record_notes", "").strip()

        if (not admission_number or not full_name or not class_level
                or len(admission_number) > 50 or len(full_name) > 150 or len(class_level) > 50
                or len(guardian_name) > 150 or len(guardian_phone) > 30
                or len(previous_school) > 150 or len(emergency_contact_name) > 150
                or len(emergency_contact_phone) > 30 or len(record_notes) > 500):
            flash("Check required fields and ensure all values are within the allowed length.", "danger")
            return redirect(url_for("register_student"))

        # Validate untrusted form input on the server; browser validation is not sufficient.
        if not admission_number.replace("-", "").replace("_", "").isalnum():
            flash("Admission number may contain only letters, numbers, hyphens, and underscores.", "danger")
            return redirect(url_for("register_student"))
        if any(ord(ch) < 32 or ord(ch) == 127
               for value in (full_name, class_level, guardian_name, previous_school, emergency_contact_name)
               for ch in value):
            flash("Names and school fields contain invalid control characters.", "danger")
            return redirect(url_for("register_student"))
        for phone_field, phone_value in (
            ("guardian", guardian_phone),
            ("emergency contact", emergency_contact_phone),
        ):
            if phone_value and any(ch not in "0123456789+()- ." for ch in phone_value):
                flash("Enter a valid " + phone_field + " phone number.", "danger")
                return redirect(url_for("register_student"))
        for date_field, date_value in (
            ("date of birth", date_of_birth),
            ("enrollment date", enrollment_date),
        ):
            if date_value:
                try:
                    parsed_date = datetime.strptime(date_value, "%Y-%m-%d").date()
                    if parsed_date.isoformat() != date_value:
                        raise ValueError
                    if date_field == "date of birth" and parsed_date > datetime.now().date():
                        raise ValueError
                except (ValueError, TypeError):
                    message = ("Enter a valid date of birth that is not in the future."
                               if date_field == "date of birth"
                               else "Enter a valid enrollment date in YYYY-MM-DD format.")
                    flash(message, "danger")
                    return redirect(url_for("register_student"))

        connection = get_db_connection()
        try:
            cursor = connection.execute(
                """
                INSERT INTO students (
                    admission_number, full_name, class_level, date_of_birth,
                    guardian_name, guardian_phone, enrollment_date, previous_school,
                    emergency_contact_name, emergency_contact_phone, record_notes, school_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (admission_number, full_name, class_level, date_of_birth or None,
                 guardian_name or None, guardian_phone or None, enrollment_date or None,
                 previous_school or None, emergency_contact_name or None,
                 emergency_contact_phone or None, record_notes or None, require_school_context()),
            )
            student_id_number = f"CMHS-{datetime.now().year}-{cursor.lastrowid:05d}"
            connection.execute(
                "UPDATE students SET student_id_number = ? WHERE id = ? AND school_id = ?",
                (student_id_number, cursor.lastrowid, require_school_context()),
            )
            connection.commit()
            write_audit(current_user.id, "student_registered", admission_number,
                        "Fictional/demo record created")
            flash("Student registered successfully.", "success")
            return redirect(url_for("list_students"))
        except sqlite3.IntegrityError:
            flash("That admission number already exists.", "danger")
            return redirect(url_for("register_student"))
        finally:
            connection.close()

    return render_template("student_register.html")


@app.route("/students/<int:student_id>", methods=["GET"])
@login_required
def student_detail(student_id):
    if current_user.role not in STUDENT_RECORD_ROLES:
        abort(403)
    connection = get_db_connection()
    try:
        # Only the principal and registrar need the full private profile. Other
        # instructional roles receive NULLs from SQL, not merely hidden template fields.
        private_profile_fields = (
            "s.date_of_birth, s.previous_school, s.guardian_name, s.guardian_phone, "
            "s.emergency_contact_name, s.emergency_contact_phone, s.record_notes"
            if current_user.role in {"principal", "registrar"}
            else "NULL AS date_of_birth, NULL AS previous_school, NULL AS guardian_name, "
                 "NULL AS guardian_phone, NULL AS emergency_contact_name, "
                 "NULL AS emergency_contact_phone, NULL AS record_notes"
        )
        student = connection.execute(
            f"""SELECT s.id, s.student_id_number, s.admission_number, s.full_name,
                       s.class_level, {private_profile_fields}, s.enrollment_date,
                       s.created_at, s.status
                FROM students s
                WHERE s.id = ? AND s.school_id = ?""",
            (student_id, require_school_context()),
        ).fetchone()
        if student is None:
            abort(404)
        finance_summary = None
        if current_user.role in {"principal", "registrar"}:
            finance_summary = connection.execute(
                """SELECT COALESCE(total_fee, 0) AS total_fee,
                          COALESCE(amount_paid, 0) AS amount_paid,
                          COALESCE(total_fee, 0) - COALESCE(amount_paid, 0) AS balance
                   FROM student_finances WHERE student_id = ? AND school_id = ? AND academic_year = ?""",
                (student_id, require_school_context(), f"{datetime.now().year}-{datetime.now().year + 1}"),
            ).fetchone()
        attendance = connection.execute(
            """SELECT status, COUNT(*) AS total FROM attendance
               WHERE student_id = ? AND school_id = ? GROUP BY status""", (student_id, require_school_context())
        ).fetchall()
        recent_grades = connection.execute(
            """SELECT subject, assessment, score, max_score, academic_year, term, created_at
               FROM grades WHERE student_id = ? AND school_id = ?
               ORDER BY created_at DESC, id DESC LIMIT 12""", (student_id, require_school_context())
        ).fetchall()
        recent_attendance = connection.execute(
            """SELECT attendance_date, status, note FROM attendance
               WHERE student_id = ? AND school_id = ? ORDER BY attendance_date DESC LIMIT 10""", (student_id, require_school_context())
        ).fetchall()
    finally:
        connection.close()
    return render_template("student_detail.html", student=student, finance_summary=finance_summary,
                           attendance=attendance, recent_grades=recent_grades,
                           recent_attendance=recent_attendance)


@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@login_required
def edit_student(student_id):
    if current_user.role not in {"principal", "registrar"}:
        abort(403)
    connection = get_db_connection()
    try:
        student = connection.execute("SELECT * FROM students WHERE id = ? AND school_id = ?", (student_id, require_school_context())).fetchone()
        if student is None:
            abort(404)
        if request.method == "POST":
            fields = {
                "full_name": request.form.get("full_name", "").strip(),
                "class_level": request.form.get("class_level", "").strip(),
                "date_of_birth": request.form.get("date_of_birth", "").strip(),
                "guardian_name": request.form.get("guardian_name", "").strip(),
                "guardian_phone": request.form.get("guardian_phone", "").strip(),
                "enrollment_date": request.form.get("enrollment_date", "").strip(),
                "previous_school": request.form.get("previous_school", "").strip(),
                "emergency_contact_name": request.form.get("emergency_contact_name", "").strip(),
                "emergency_contact_phone": request.form.get("emergency_contact_phone", "").strip(),
                "record_notes": request.form.get("record_notes", "").strip(),
            }
            limits = {"full_name":150,"class_level":50,"guardian_name":150,"guardian_phone":30,
                      "previous_school":150,"emergency_contact_name":150,
                      "emergency_contact_phone":30,"record_notes":500,"date_of_birth":10,"enrollment_date":10}
            if not fields["full_name"] or not fields["class_level"] or any(len(v)>limits[k] for k,v in fields.items()):
                flash("Name and class are required. Please check field lengths.", "danger")
                return redirect(url_for("edit_student", student_id=student_id))
            if any(ord(ch) < 32 or ord(ch) == 127
                   for key in ("full_name", "class_level", "guardian_name", "previous_school", "emergency_contact_name")
                   for ch in fields[key]):
                flash("Name and school fields contain invalid control characters.", "danger")
                return redirect(url_for("edit_student", student_id=student_id))
            for phone_field in ("guardian_phone", "emergency_contact_phone"):
                if fields[phone_field] and any(ch not in "0123456789+()- ." for ch in fields[phone_field]):
                    flash("Enter a valid phone number.", "danger")
                    return redirect(url_for("edit_student", student_id=student_id))
            for date_field in ("date_of_birth", "enrollment_date"):
                if fields[date_field]:
                    try:
                        parsed_date = datetime.strptime(fields[date_field], "%Y-%m-%d").date()
                        if parsed_date.isoformat() != fields[date_field]:
                            raise ValueError
                        if date_field == "date_of_birth" and parsed_date > datetime.now().date():
                            raise ValueError
                    except ValueError:
                        message = ("Date of birth cannot be in the future." if date_field == "date_of_birth"
                                   else "Dates must use the valid YYYY-MM-DD format.")
                        flash(message, "danger")
                        return redirect(url_for("edit_student", student_id=student_id))
            old_class = student["class_level"]
            connection.execute(
                """UPDATE students SET full_name=?, class_level=?, date_of_birth=?,
                   guardian_name=?, guardian_phone=?, enrollment_date=?, previous_school=?,
                   emergency_contact_name=?, emergency_contact_phone=?, record_notes=?
                   WHERE id=? AND school_id=?""",
                (fields["full_name"], fields["class_level"], fields["date_of_birth"] or None,
                 fields["guardian_name"] or None, fields["guardian_phone"] or None,
                 fields["enrollment_date"] or None, fields["previous_school"] or None,
                 fields["emergency_contact_name"] or None, fields["emergency_contact_phone"] or None,
                 fields["record_notes"] or None, student_id, require_school_context()),
            )
            connection.commit()
            write_audit(current_user.id, "student_record_updated", student["admission_number"],
                        f"class changed from {old_class} to {fields['class_level']}")
            flash("Student record updated successfully.", "success")
            return redirect(url_for("student_detail", student_id=student_id))
    finally:
        connection.close()
    return render_template("student_edit.html", student=student)


@app.route("/students")
@login_required
def list_students():
    if current_user.role not in STUDENT_RECORD_ROLES:
        flash("You are not authorized to view student records.", "danger")
        return redirect(url_for("dashboard"))

    class_filter = request.args.get("class_level", "").strip()
    search_query = request.args.get("q", "").strip()[:100]
    connection = get_db_connection()
    try:
        # Teachers need class/identity details for instruction, not private guardian
        # contact information or student birth dates. Expressions are fixed constants.
        private_student_fields = (
            "s.date_of_birth, s.guardian_name, s.guardian_phone"
            if current_user.role in {"principal", "registrar"}
            else "NULL AS date_of_birth, NULL AS guardian_name, NULL AS guardian_phone"
        )
        # Do not fetch fee/payment data for roles that cannot view finances. Hiding
        # columns in the template is not a substitute for least-privilege data access.
        can_view_finances = current_user.role in {"principal", "registrar"}
        finance_columns = (
            "COALESCE(f.total_fee, 0) AS total_fee, "
            "COALESCE(f.amount_paid, 0) AS amount_paid, "
            "COALESCE(f.total_fee, 0) - COALESCE(f.amount_paid, 0) AS balance"
            if can_view_finances
            else "NULL AS total_fee, NULL AS amount_paid, NULL AS balance"
        )
        finance_join = (
            """LEFT JOIN student_finances f
                   ON f.student_id = s.id AND f.school_id = s.school_id
                  AND f.academic_year = ?"""
            if can_view_finances
            else ""
        )
        query_params = []
        if can_view_finances:
            query_params.append(f"{datetime.now().year}-{datetime.now().year + 1}")
        query_params.extend([
            require_school_context(), class_filter, class_filter,
            search_query, f"%{search_query}%", f"%{search_query}%", f"%{search_query}%",
        ])
        students = connection.execute(
            f"""
            SELECT s.id, s.student_id_number, s.admission_number, s.full_name, s.class_level,
                   {private_student_fields}, s.status, {finance_columns}
            FROM students s
            {finance_join}
            WHERE s.school_id = ? AND (? = '' OR s.class_level = ?)
              AND (? = '' OR s.full_name LIKE ? OR s.admission_number LIKE ?
                   OR COALESCE(s.student_id_number, '') LIKE ?)
            ORDER BY s.class_level, s.full_name COLLATE NOCASE
            """,
            query_params,
        ).fetchall()
        classes = connection.execute(
            "SELECT DISTINCT class_level FROM students WHERE school_id = ? ORDER BY class_level COLLATE NOCASE",
            (require_school_context(),),
        ).fetchall()
    finally:
        connection.close()
    return render_template("students.html", students=students, classes=classes,
                           class_filter=class_filter, search_query=search_query,
                           can_view_finances=current_user.role in {"principal", "registrar"})


@app.route("/student-finances", methods=["GET", "POST"])
@login_required
def student_finances():
    if current_user.role not in {"super_admin", "school_admin", "principal", "registrar", "finance_admin"}:
        abort(403)
    school_id = require_school_context()

    connection = get_db_connection()
    try:
        # This page can be the first finance module opened on a fresh install.
        # Ensure the payment ledger exists before checking it below.
        ensure_content_tables(connection)
        if request.method == "POST":
            # Serialize financial validation and writes so concurrent requests
            # cannot overwrite or spend the same balance based on stale reads.
            connection.execute("BEGIN IMMEDIATE")
            student_id = request.form.get("student_id", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            total_text = request.form.get("total_fee", "").strip()
            paid_text = request.form.get("amount_paid", "").strip()
            notes = request.form.get("notes", "").strip()
            valid_year = (len(academic_year) == 9 and academic_year[4] == "-"
                          and academic_year[:4].isdigit() and academic_year[5:].isdigit()
                          and int(academic_year[5:]) == int(academic_year[:4]) + 1)
            student = connection.execute(
                "SELECT id, admission_number FROM students WHERE id = ? AND school_id = ? AND status = 'active'",
                (student_id, require_school_context()),
            ).fetchone()
            try:
                total_decimal = Decimal(total_text)
                paid_decimal = Decimal(paid_text)
                if (
                    not total_decimal.is_finite() or not paid_decimal.is_finite()
                    or total_decimal.as_tuple().exponent < -2
                    or paid_decimal.as_tuple().exponent < -2
                    or total_decimal < 0 or paid_decimal < 0
                    or total_decimal > Decimal("100000000")
                    or paid_decimal > Decimal("100000000")
                ):
                    raise ValueError
                # Bound Decimal values before converting to int so maliciously
                # large exponents cannot trigger enormous integer allocations.
                total_cents = int(total_decimal * 100)
                paid_cents = int(paid_decimal * 100)
                if not (
                    0 <= paid_cents <= total_cents <= 10000000000
                    and len(notes) <= 250 and valid_year and student
                ):
                    raise ValueError
                total_fee, amount_paid = total_cents / 100, paid_cents / 100
            except (InvalidOperation, ValueError, TypeError):
                flash("Check the student, academic year, fee and payment amounts (maximum two decimal places). Payments cannot exceed the total fees.", "danger")
                return redirect(url_for("student_finances"))

            # Once a fee record has a payment ledger, its paid total must continue
            # to agree with the ledger. Never silently erase or rewrite receipts by
            # editing the summary form; record new payments in the payment workflow.
            ledger = connection.execute(
                """SELECT COUNT(*) AS payment_count,
                          COALESCE(SUM(CAST(ROUND(amount * 100) AS INTEGER)), 0) AS paid_cents
                   FROM finance_payments
                   WHERE student_id=? AND school_id=? AND academic_year=?""",
                (student_id, school_id, academic_year),
            ).fetchone()
            if ledger["payment_count"] and (
                paid_cents != ledger["paid_cents"] or total_cents < ledger["paid_cents"]
            ):
                flash("This student has recorded payment receipts. The paid total must match the payment history; use the payment workflow to record additional payments.", "danger")
                return redirect(url_for("student_finances", academic_year=academic_year))
            connection.execute(
                """INSERT INTO student_finances
                   (student_id, academic_year, total_fee, amount_paid, notes, updated_by, school_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(student_id, academic_year) DO UPDATE SET
                     total_fee = excluded.total_fee, amount_paid = excluded.amount_paid,
                     notes = excluded.notes, updated_by = excluded.updated_by,
                     updated_at = CURRENT_TIMESTAMP""",
                (student_id, academic_year, total_fee, amount_paid, notes or None, current_user.id, school_id),
            )
            connection.commit()
            write_audit(current_user.id, "student_finance_updated", student["admission_number"],
                        f"{academic_year}; fees={total_fee:.2f}; paid={amount_paid:.2f}")
            flash("Student financial record saved.", "success")
            return redirect(url_for("student_finances", academic_year=academic_year))

        academic_year = request.args.get("academic_year", f"{datetime.now().year}-{datetime.now().year + 1}")
        valid_year = (len(academic_year) == 9 and academic_year[4] == "-"
                      and academic_year[:4].isdigit() and academic_year[5:].isdigit()
                      and int(academic_year[5:]) == int(academic_year[:4]) + 1)
        if not valid_year:
            academic_year = f"{datetime.now().year}-{datetime.now().year + 1}"
        students = connection.execute(
            "SELECT id, student_id_number, admission_number, full_name, class_level FROM students "
            "WHERE school_id = ? AND status = 'active' ORDER BY class_level, full_name",
            (require_school_context(),),
        ).fetchall()
        records = connection.execute(
            """SELECT s.student_id_number, s.admission_number, s.full_name, s.class_level,
                      f.total_fee, f.amount_paid, f.total_fee - f.amount_paid AS balance,
                      f.updated_at, f.notes
               FROM student_finances f JOIN students s ON s.id = f.student_id AND s.school_id = f.school_id
               WHERE f.school_id = ? AND s.school_id = ? AND f.academic_year = ? ORDER BY s.class_level, s.full_name COLLATE NOCASE""",
            (require_school_context(), require_school_context(), academic_year),
        ).fetchall()
        summary = connection.execute(
            """SELECT COALESCE(SUM(total_fee), 0) AS total_fees,
                      COALESCE(SUM(amount_paid), 0) AS total_paid,
                      COALESCE(SUM(total_fee - amount_paid), 0) AS total_balance
               FROM student_finances WHERE school_id = ? AND academic_year = ?""",
            (require_school_context(), academic_year),
        ).fetchone()
    finally:
        connection.close()
    return render_template("student_finances.html", students=students, records=records,
                           academic_year=academic_year, summary=summary)


# --------------------------------------------------
# VERSION 0.3 SCHOOL CONTENT MODULES
# --------------------------------------------------
RESOURCE_DIR = INSTANCE_DIR / "learning_materials"
RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_RESOURCE_EXTENSIONS = {".pdf", ".txt", ".png", ".jpg", ".jpeg"}
CONTENT_PUBLISH_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal",
    "academic_admin", "registrar", "teacher",
}
CONTENT_MANAGE_ROLES = {
    "super_admin", "school_admin", "principal", "vice_principal",
    "academic_admin", "registrar",
}
MATERIALS_PUBLISH_ROLES = CONTENT_PUBLISH_ROLES | {"librarian"}
MATERIALS_MANAGE_ROLES = CONTENT_MANAGE_ROLES | {"librarian"}

def ensure_content_tables(connection):
    """Create tenant-scoped content tables without altering existing school data."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS learning_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            class_level TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            original_name TEXT NOT NULL,
            uploaded_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS school_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            audience TEXT NOT NULL CHECK (audience IN ('public','students','staff','all')),
            status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('published','archived')),
            created_by INTEGER NOT NULL REFERENCES users(id),
            updated_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS finance_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            student_id INTEGER NOT NULL,
            academic_year TEXT NOT NULL,
            amount REAL NOT NULL CHECK (amount > 0),
            method TEXT NOT NULL CHECK (method IN ('cash','bank_transfer','mobile_money','other')),
            reference TEXT,
            receipt_number TEXT NOT NULL UNIQUE,
            received_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id)
        )
    """)
    connection.commit()

def can_view_material(role, class_level, archived=False):
    if archived:
        return role in MATERIALS_MANAGE_ROLES
    if role in MATERIALS_PUBLISH_ROLES:
        return True
    if role in {"student", "parent"}:
        # Until student/parent accounts have a verified enrollment link, only school-wide
        # materials are exposed; never trust a class supplied by the browser.
        return class_level.lower() in {"all classes", "school-wide"}
    return False

@app.route("/learning-materials", methods=["GET", "POST"])
@login_required
def learning_materials():
    school_id = require_school_context()
    role = current_user.role
    connection = get_db_connection()
    try:
        ensure_content_tables(connection)
        if request.method == "POST":
            if role not in MATERIALS_PUBLISH_ROLES:
                abort(403)
            upload = request.files.get("resource_file")
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            class_level = request.form.get("class_level", "").strip()
            original = secure_filename(upload.filename or "") if upload else ""
            suffix = Path(original).suffix.lower()
            if (not title or len(title) > 120 or len(description) > 500
                or not class_level or len(class_level) > 60 or not upload
                or not original or suffix not in ALLOWED_RESOURCE_EXTENSIONS):
                flash("Enter a title and class, and upload a PDF, TXT, PNG or JPG file.", "danger")
                return redirect(url_for("learning_materials"))
            # Store under a random server-generated name; never use user paths.
            stored_name = uuid.uuid4().hex + suffix
            target = RESOURCE_DIR / stored_name
            upload.save(target)
            connection.execute(
                """INSERT INTO learning_materials
                   (school_id,title,description,class_level,stored_name,original_name,uploaded_by)
                   VALUES (?,?,?,?,?,?,?)""",
                (school_id, title, description, class_level, stored_name, original,
                 int(current_user.id)),
            )
            connection.commit()
            write_audit(current_user.id, "learning_material_uploaded", original,
                        "class=" + class_level)
            flash("Learning material uploaded.", "success")
            return redirect(url_for("learning_materials"))
        rows = connection.execute(
            """SELECT id,title,description,class_level,original_name,created_at,archived
               FROM learning_materials WHERE school_id = ?
               ORDER BY archived, created_at DESC, id DESC""", (school_id,)
        ).fetchall()
        visible = [row for row in rows if can_view_material(role, row["class_level"], bool(row["archived"]))]
    finally:
        connection.close()
    return render_template("learning_materials.html", materials=visible,
                           can_upload=role in MATERIALS_PUBLISH_ROLES,
                           can_manage=role in MATERIALS_MANAGE_ROLES)

@app.route("/learning-materials/<int:material_id>/download")
@login_required
def download_learning_material(material_id):
    school_id = require_school_context()
    connection = get_db_connection()
    try:
        ensure_content_tables(connection)
        material = connection.execute(
            "SELECT * FROM learning_materials WHERE id=? AND school_id=?",
            (material_id, school_id),
        ).fetchone()
        if material is None or not can_view_material(
            current_user.role, material["class_level"], bool(material["archived"])
        ):
            abort(404)
        path = RESOURCE_DIR / material["stored_name"]
        if not path.is_file() or path.parent.resolve() != RESOURCE_DIR.resolve():
            abort(404)
        return send_file(path, as_attachment=True,
                         download_name=material["original_name"],
                         mimetype="application/octet-stream", conditional=True)
    finally:
        connection.close()

@app.route("/learning-materials/<int:material_id>/archive", methods=["POST"])
@login_required
def archive_learning_material(material_id):
    if current_user.role not in MATERIALS_MANAGE_ROLES:
        abort(403)
    school_id = require_school_context()
    connection = get_db_connection()
    try:
        ensure_content_tables(connection)
        result = connection.execute(
            "UPDATE learning_materials SET archived=1 WHERE id=? AND school_id=?",
            (material_id, school_id),
        )
        connection.commit()
        if result.rowcount != 1:
            abort(404)
    finally:
        connection.close()
    write_audit(current_user.id, "learning_material_archived", str(material_id))
    flash("Learning material archived.", "success")
    return redirect(url_for("learning_materials"))

@app.route("/announcements", methods=["GET", "POST"])
@login_required
def announcements():
    school_id = require_school_context()
    role = current_user.role
    connection = get_db_connection()
    try:
        ensure_content_tables(connection)
        if request.method == "POST":
            if role not in CONTENT_PUBLISH_ROLES:
                abort(403)
            title = request.form.get("title", "").strip()
            body = request.form.get("body", "").strip()
            audience = request.form.get("audience", "all").strip()
            edit_id = request.form.get("announcement_id", "").strip()
            if not title or len(title) > 140 or not body or len(body) > 5000 or audience not in {"public","students","staff","all"}:
                flash("Check the announcement title, message length and audience.", "danger")
                return redirect(url_for("announcements"))
            if edit_id:
                if role not in CONTENT_MANAGE_ROLES or not edit_id.isdigit():
                    abort(403)
                result = connection.execute(
                    """UPDATE school_announcements SET title=?,body=?,audience=?,
                       updated_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND school_id=? AND status='published'""",
                    (title, body, audience, int(current_user.id), int(edit_id), school_id),
                )
                if result.rowcount != 1:
                    abort(404)
                action = "announcement_updated"
            else:
                connection.execute(
                    """INSERT INTO school_announcements
                       (school_id,title,body,audience,created_by,updated_by)
                       VALUES (?,?,?,?,?,?)""",
                    (school_id, title, body, audience, int(current_user.id), int(current_user.id)),
                )
                action = "announcement_published"
            connection.commit()
            write_audit(current_user.id, action, title)
            flash("Announcement saved.", "success")
            return redirect(url_for("announcements"))
        if role in CONTENT_MANAGE_ROLES:
            rows = connection.execute(
                """SELECT * FROM school_announcements WHERE school_id=?
                   ORDER BY CASE status WHEN 'published' THEN 0 ELSE 1 END, updated_at DESC""",
                (school_id,),
            ).fetchall()
        else:
            allowed = ("students", "all") if role in {"student", "parent"} else ("staff", "all")
            rows = connection.execute(
                """SELECT * FROM school_announcements WHERE school_id=? AND status='published'
                   AND audience IN (?,?) ORDER BY updated_at DESC""",
                (school_id, *allowed),
            ).fetchall()
    finally:
        connection.close()
    return render_template("announcements.html", announcements=rows,
                           can_publish=role in CONTENT_PUBLISH_ROLES,
                           can_manage=role in CONTENT_MANAGE_ROLES)

@app.route("/announcements/<int:announcement_id>/archive", methods=["POST"])
@login_required
def archive_announcement(announcement_id):
    if current_user.role not in CONTENT_MANAGE_ROLES:
        abort(403)
    school_id = require_school_context()
    connection = get_db_connection()
    try:
        ensure_content_tables(connection)
        result = connection.execute(
            """UPDATE school_announcements SET status='archived',updated_by=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=? AND status='published'""",
            (int(current_user.id), announcement_id, school_id),
        )
        connection.commit()
        if result.rowcount != 1:
            abort(404)
    finally:
        connection.close()
    write_audit(current_user.id, "announcement_archived", str(announcement_id))
    flash("Announcement archived.", "success")
    return redirect(url_for("announcements"))

@app.route("/finance/payments", methods=["GET", "POST"])
@login_required
def finance_payments():
    if current_user.role not in {"super_admin", "school_admin", "principal", "registrar", "finance_admin"}:
        abort(403)
    school_id = require_school_context()
    connection = get_db_connection()
    try:
        ensure_content_tables(connection)
        if request.method == "POST":
            # Lock the SQLite write transaction before reading the outstanding
            # balance; this prevents concurrent payments from overspending it.
            connection.execute("BEGIN IMMEDIATE")
            student_id = request.form.get("student_id", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            amount_text = request.form.get("amount", "").strip()
            method = request.form.get("method", "").strip()
            reference = request.form.get("reference", "").strip()[:100]
            valid_year = (len(academic_year) == 9 and academic_year[4] == "-"
                          and academic_year[:4].isdigit() and academic_year[5:].isdigit()
                          and int(academic_year[5:]) == int(academic_year[:4]) + 1)
            student = connection.execute(
                "SELECT id,admission_number FROM students WHERE id=? AND school_id=? AND status='active'",
                (student_id, school_id),
            ).fetchone()
            try:
                amount_decimal = Decimal(amount_text)
                if (
                    not amount_decimal.is_finite()
                    or amount_decimal.as_tuple().exponent < -2
                    or amount_decimal <= 0
                    or amount_decimal > Decimal("100000000")
                ):
                    raise ValueError
                # Reject out-of-range values before converting Decimal to int.
                amount_cents = int(amount_decimal * 100)
                if not student or not valid_year or not (0 < amount_cents <= 10000000000) or method not in {"cash","bank_transfer","mobile_money","other"}:
                    raise ValueError
                amount = amount_cents / 100
            except (InvalidOperation, ValueError, TypeError):
                flash("Check the student, academic year, payment amount (maximum two decimal places) and payment method.", "danger")
                return redirect(url_for("finance_payments"))
            finance = connection.execute(
                "SELECT total_fee,amount_paid FROM student_finances WHERE student_id=? AND school_id=? AND academic_year=?",
                (student_id, school_id, academic_year),
            ).fetchone()
            if not finance:
                flash("Set up the student's fee record before recording a payment.", "danger")
                return redirect(url_for("student_finances", academic_year=academic_year))
            # Compare integer cents rather than binary floats, preventing a
            # fractional-cent overpayment from slipping through a tolerance.
            total_fee_cents = int(round(float(finance["total_fee"]) * 100))
            paid_cents = int(round(float(finance["amount_paid"]) * 100))
            if amount_cents > total_fee_cents - paid_cents:
                flash("Payment exceeds the outstanding balance. Reconcile the fee record first.", "danger")
                return redirect(url_for("finance_payments"))
            receipt = "RCPT-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:10].upper()
            connection.execute(
                """INSERT INTO finance_payments
                   (school_id,student_id,academic_year,amount,method,reference,receipt_number,received_by)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (school_id, student_id, academic_year, amount, method, reference or None, receipt, int(current_user.id)),
            )
            connection.execute(
                """UPDATE student_finances SET amount_paid=amount_paid+?,updated_by=?,
                   updated_at=CURRENT_TIMESTAMP WHERE student_id=? AND school_id=? AND academic_year=?""",
                (amount, int(current_user.id), student_id, school_id, academic_year),
            )
            connection.commit()
            write_audit(current_user.id, "finance_payment_recorded", receipt,
                        f"student={student['admission_number']}; amount={amount:.2f}; method={method}")
            flash("Payment recorded. Receipt number: " + receipt, "success")
            return redirect(url_for("finance_payments", receipt=receipt))
        students = connection.execute(
            "SELECT id,admission_number,full_name,class_level FROM students WHERE school_id=? AND status='active' ORDER BY class_level,full_name",
            (school_id,),
        ).fetchall()
        payments = connection.execute(
            """SELECT p.receipt_number,p.amount,p.method,p.reference,p.academic_year,p.created_at,
                      s.full_name,s.admission_number,s.class_level
               FROM finance_payments p JOIN students s ON s.id=p.student_id AND s.school_id=p.school_id
               WHERE p.school_id=? ORDER BY p.id DESC LIMIT 100""", (school_id,)
        ).fetchall()
        receipt = request.args.get("receipt", "").strip()
        receipt_record = connection.execute(
            """SELECT p.*,s.full_name,s.admission_number,s.class_level
               FROM finance_payments p JOIN students s ON s.id=p.student_id AND s.school_id=p.school_id
               WHERE p.school_id=? AND p.receipt_number=?""", (school_id, receipt)
        ).fetchone() if receipt else None
    finally:
        connection.close()
    return render_template("finance_payments.html", students=students, payments=payments,
                           receipt=receipt_record,
                           current_year=f"{datetime.now().year}-{datetime.now().year + 1}")


@app.route("/parent/children")
@login_required
def parent_children():
    """Show a parent only the children explicitly linked to that account."""
    if current_user.role != "parent":
        abort(403)
    school_id = require_school_context()
    connection = get_db_connection()
    try:
        children = connection.execute(
            """SELECT s.id, s.full_name, s.admission_number, s.class_level,
                      s.student_id_number
               FROM parent_student_links l
               JOIN students s ON s.id = l.student_id AND s.school_id = l.school_id
               JOIN users p ON p.id = l.parent_user_id AND p.school_id = l.school_id
               WHERE l.parent_user_id = ? AND l.school_id = ?
                 AND p.status = 'active' AND s.status = 'active'
               ORDER BY s.full_name COLLATE NOCASE""",
            (current_user.id, school_id),
        ).fetchall()
        child_ids = [child["id"] for child in children]
        report_rows = []
        attendance_rows = []
        finance_rows = []
        if child_ids:
            placeholders = ",".join("?" for _ in child_ids)
            params = [school_id, *child_ids]
            report_rows = connection.execute(
                f"""SELECT s.id AS student_id, s.full_name, rc.academic_year, rc.term,
                           rc.published_at, g.subject, g.assessment, g.score, g.max_score
                    FROM report_cards rc
                    JOIN students s ON s.id = rc.student_id AND s.school_id = rc.school_id
                    JOIN grades g ON g.student_id = rc.student_id
                        AND g.school_id = rc.school_id
                        AND g.academic_year = rc.academic_year AND g.term = rc.term
                    WHERE rc.school_id = ? AND s.id IN ({placeholders}) AND rc.published = 1
                    ORDER BY s.full_name, rc.academic_year DESC, rc.term, g.subject""",
                params,
            ).fetchall()
            attendance_rows = connection.execute(
                f"""SELECT s.id AS student_id, s.full_name, a.attendance_date, a.status
                    FROM attendance a
                    JOIN students s ON s.id = a.student_id AND s.school_id = a.school_id
                    WHERE a.school_id = ? AND s.id IN ({placeholders})
                    ORDER BY a.attendance_date DESC LIMIT 100""",
                params,
            ).fetchall()
            academic_year = f"{datetime.now().year}-{datetime.now().year + 1}"
            finance_rows = connection.execute(
                f"""SELECT s.id AS student_id, s.full_name, f.academic_year,
                           f.total_fee, f.amount_paid,
                           (f.total_fee - f.amount_paid) AS balance
                    FROM student_finances f
                    JOIN students s ON s.id = f.student_id AND s.school_id = f.school_id
                    WHERE f.school_id = ? AND s.id IN ({placeholders}) AND f.academic_year = ?
                    ORDER BY s.full_name""",
                [school_id, *child_ids, academic_year],
            ).fetchall()
    finally:
        connection.close()
    return render_template(
        "parent_children.html", children=children, report_rows=report_rows,
        attendance_rows=attendance_rows, finance_rows=finance_rows,
    )



from homework import register_homework_routes
register_homework_routes(globals())
from staff_attendance import register_staff_attendance_routes
register_staff_attendance_routes(globals())
from school_performance import register_school_performance_routes
register_school_performance_routes(globals())
from demo_staff import register_demo_staff_routes
register_demo_staff_routes(globals())



if __name__ == "__main__":
    app.run(port=5000, debug=False)
