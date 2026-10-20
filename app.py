import os
import secrets
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

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
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

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
    "principal": "Principal / School Administrator",
    "registrar": "Registrar / Student Records Officer",
    "academic_admin": "Vice Principal / Academic Administrator",
    "finance_admin": "Finance Officer",
    "teacher": "Teacher",
}
STUDENT_RECORD_ROLES = {"super_admin", "principal", "registrar", "academic_admin", "teacher"}


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
    if (
        "super_admin" in schema_sql
        and {"status", "is_super_admin", "created_at", "approved_by", "approved_at", "full_name"}.issubset(columns)
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
                role IN ('super_admin', 'principal', 'registrar',
                         'academic_admin', 'finance_admin', 'teacher', 'student')
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
        connection.execute(
            f"""
            INSERT INTO users_migrating
                (id, username, full_name, password_hash, role, status, is_super_admin,
                 created_at, approved_by, approved_at)
            SELECT id, username, {full_name_expr}, password_hash,
                   CASE WHEN role = 'admin' THEN 'super_admin' ELSE role END,
                   {status_expr},
                   CASE WHEN role = 'admin' THEN 1 ELSE {super_expr} END,
                   {created_expr}, {approved_by_expr}, {approved_at_expr}
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
            UNIQUE(student_id, subject, assessment, academic_year, term)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO grades_migrating
            (id, student_id, subject, assessment, score, max_score,
             recorded_by, created_at, academic_year, term)
        SELECT id, student_id, subject, assessment, score, max_score,
               recorded_by, created_at, academic_year, term
        FROM grades
        """
    )
    connection.execute("DROP TABLE grades")
    connection.execute("ALTER TABLE grades_migrating RENAME TO grades")


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
                    role IN ('super_admin', 'principal', 'registrar',                             'academic_admin', 'finance_admin', 'teacher', 'student')
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
            "INSERT INTO schools (id, name, slug) VALUES (1, 'Legacy Demo Campus', 'legacy-demo-campus') "
            "ON CONFLICT(id) DO NOTHING"
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
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
    finally:
        connection.close()


initialize_database()


def write_audit(actor_user_id, action, target_username=None, details=None):
    connection = get_db_connection()
    try:
        connection.execute(
            """
            INSERT INTO audit_log (actor_user_id, action, target_username, details)
            VALUES (?, ?, ?, ?)
            """,
            (actor_user_id, action, target_username, details),
        )
        connection.commit()
    finally:
        connection.close()


# --------------------------------------------------
# 3. USER MODEL AND DATABASE LOOKUPS
# --------------------------------------------------

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
            SELECT id, username, password_hash, role, is_super_admin, school_id
            FROM users WHERE id = ? AND status = 'active'
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
            SELECT id, username, password_hash, role, is_super_admin, school_id
            FROM users WHERE username = ? AND status = 'active'
            """,
            (username,),
        ).fetchone()
        if row is None:
            return None
        return User(row["id"], row["username"], row["password_hash"],
                    row["role"], row["is_super_admin"])
    finally:
        connection.close()


@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)


def require_super_admin():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    if not current_user.is_super_admin:
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


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Please enter your username and password.")
            return render_template("login.html")

        user = get_user_by_username(username)
        if user and check_password_hash(user.password_hash, password):
            session.permanent = True
            login_user(user)
            write_audit(user.id, "login_success", user.username)
            return redirect(url_for("dashboard"))
        write_audit(None, "login_failed", username[:64] or None)
        flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        name=current_user.username,
        role=current_user.role,
    )


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    write_audit(current_user.id, "logout", current_user.username)
    logout_user()
    return redirect(url_for("index"))


# --------------------------------------------------
# 5. ADMINISTRATOR REQUEST AND MANAGEMENT
# --------------------------------------------------

@app.route("/admin/request-access", methods=["GET", "POST"])
def request_staff_access():
    """Collect a staff access request; requests never grant login access."""
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        requested_role = request.form.get("requested_role", "").strip()

        if not full_name or not username or requested_role not in STAFF_ROLES:
            flash("Complete all fields and choose a valid staff role.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES)

        if len(full_name) > 120 or len(username) > 64:
            flash("Name or username is too long.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES)
        if not username.replace("_", "").replace("-", "").isalnum():
            flash("Use only letters, numbers, underscores or hyphens in the username.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES)

        # Pending requests receive an unknown random password hash and cannot
        # authenticate until a Super Administrator approves them and sets a password.
        placeholder_password = generate_password_hash(secrets.token_urlsafe(48))
        connection = get_db_connection()
        try:
            connection.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role, status, is_super_admin)
                VALUES (?, ?, ?, ?, 'pending', 0)
                """,
                (username, full_name, placeholder_password, requested_role),
            )
            connection.commit()
            flash("Access request submitted. You cannot sign in until it is approved.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already in use or has a request pending.", "danger")
            return render_template("admin_request.html", roles=STAFF_ROLES)
        finally:
            connection.close()

    return render_template("admin_request.html", roles=STAFF_ROLES)


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
            target = connection.execute(
                "SELECT id, username, role, status, is_super_admin FROM users WHERE id = ?",
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
                        INSERT INTO audit_log (actor_user_id, action, target_username, details)
                        VALUES (?, 'staff_request_approved', ?, ?)
                        """,
                        (current_user.id, target_username, "Role: " + target["role"]),
                    )
                    connection.commit()
                    flash("Account approved and activated. Share the temporary password securely.", "success")
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
                    INSERT INTO audit_log (actor_user_id, action, target_username, details)
                    VALUES (?, ?, ?, ?)
                    """,
                    (current_user.id, "staff_account_" + action, target_username,
                     "Status changed to " + new_status),
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
                        "SELECT id, admission_number FROM students WHERE id = ? AND status = 'active'",
                        (student_id,),
                    ).fetchone()
                    if not student:
                        flash("Choose a valid active student record.", "danger")
                    else:
                        existing = connection.execute(
                            "SELECT account_user_id FROM students WHERE id = ?", (student_id,)
                        ).fetchone()
                        if existing["account_user_id"] not in (None, target_id_int):
                            flash("That student record is already linked to another account.", "danger")
                        else:
                            connection.execute("UPDATE students SET account_user_id = NULL WHERE account_user_id = ?", (target_id_int,))
                            connection.execute("UPDATE students SET account_user_id = ? WHERE id = ?", (target_id_int, student["id"]))
                            connection.execute(
                                "INSERT INTO audit_log (actor_user_id, action, target_username, details) VALUES (?, 'student_account_linked', ?, ?)",
                                (current_user.id, target_username, "Student record: " + student["admission_number"]),
                            )
                            connection.commit()
                            flash("Student account linked to the verified student record.", "success")
            elif action == "unlink_student":
                linked = connection.execute(
                    "SELECT admission_number FROM students WHERE account_user_id = ?", (target_id_int,)
                ).fetchone()
                if target["role"] != "student":
                    flash("Only student accounts can be unlinked from student records.", "danger")
                elif linked:
                    connection.execute("UPDATE students SET account_user_id = NULL WHERE account_user_id = ?", (target_id_int,))
                    connection.execute(
                        "INSERT INTO audit_log (actor_user_id, action, target_username, details) VALUES (?, 'student_account_unlinked', ?, ?)",
                        (current_user.id, target_username, "Student record: " + linked["admission_number"]),
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
                        "INSERT INTO audit_log (actor_user_id, action, target_username, details) VALUES (?, 'staff_role_changed', ?, ?)",
                        (current_user.id, target_username, "New role: " + new_role),
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
            FROM users u LEFT JOIN students s ON s.account_user_id = u.id
            ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                     u.username COLLATE NOCASE
            """
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
    )


# --------------------------------------------------
# 6. GRADES AND ATTENDANCE
# --------------------------------------------------

ACADEMIC_ENTRY_ROLES = {"super_admin", "teacher", "academic_admin"}
ACADEMIC_VIEW_ROLES = {"super_admin", "teacher", "academic_admin", "principal", "registrar"}


@app.route("/academics", methods=["GET", "POST"])
@login_required
def academics():
    """Record and review demo grades and daily attendance with role checks."""
    if current_user.role not in ACADEMIC_VIEW_ROLES:
        abort(403)

    connection = get_db_connection()
    try:
        students = connection.execute(
            "SELECT id, admission_number, full_name, class_level FROM students WHERE status = 'active' ORDER BY class_level, full_name"
        ).fetchall()
        if request.method == "POST":
            if current_user.role not in ACADEMIC_ENTRY_ROLES:
                abort(403)
            action = request.form.get("action", "").strip()
            student_id = request.form.get("student_id", "").strip()
            student = connection.execute(
                "SELECT id FROM students WHERE id = ? AND status = 'active'", (student_id,)
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
                       WHERE student_id = ? AND academic_year = ? AND term = ? AND published = 1""",
                    (student_id, academic_year, term),
                ).fetchone()
                if published_report:
                    flash("This term's report card is published. Unpublish it before recording additional grades.", "danger")
                    return redirect(url_for("academics"))
                try:
                    connection.execute(
                        """INSERT INTO grades
                           (student_id, subject, assessment, score, max_score, recorded_by, academic_year, term)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (student_id, subject, assessment, score, max_score, current_user.id, academic_year, term),
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
                        """INSERT INTO attendance (student_id, attendance_date, status, note, recorded_by)
                           VALUES (?, ?, ?, ?, ?)""",
                        (student_id, attendance_date, status, note or None, current_user.id),
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
               JOIN users u ON u.id = g.recorded_by ORDER BY g.id DESC LIMIT 100"""
        ).fetchall()
        attendance_rows = connection.execute(
            """SELECT a.id, s.full_name, s.admission_number, s.class_level,
                      a.attendance_date, a.status, a.note, u.username AS recorded_by
               FROM attendance a JOIN students s ON s.id = a.student_id
               JOIN users u ON u.id = a.recorded_by
               ORDER BY a.attendance_date DESC, a.id DESC LIMIT 100"""
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
# 6. REPORT CARDS
# --------------------------------------------------

REPORT_CARD_VIEW_ROLES = {"super_admin", "principal", "academic_admin", "teacher", "registrar"}
REPORT_CARD_PUBLISH_ROLES = {"super_admin", "principal", "academic_admin"}


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
               FROM students WHERE account_user_id = ? AND status = 'active'""",
            (current_user.id,),
        ).fetchone()
        reports = []
        if student:
            reports = connection.execute(
                """SELECT rc.academic_year, rc.term, rc.published_at,
                          g.subject, g.score, g.max_score, g.assessment
                   FROM report_cards rc
                   JOIN grades g ON g.student_id = rc.student_id
                       AND g.academic_year = rc.academic_year AND g.term = rc.term
                   WHERE rc.student_id = ? AND rc.published = 1
                   ORDER BY rc.academic_year DESC, rc.term, g.subject COLLATE NOCASE,
                            g.assessment COLLATE NOCASE""",
                (student["id"],),
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
            "SELECT id, admission_number, full_name, class_level FROM students WHERE status = 'active' ORDER BY class_level, full_name"
        ).fetchall()
        student_id = request.values.get("student_id", "").strip()
        academic_year = request.values.get("academic_year", "").strip()
        term = request.values.get("term", "").strip()
        selected_student = None
        subject_rows = []
        publication = None

        if student_id and academic_year and term in {"Term 1", "Term 2", "Term 3"}:
            selected_student = connection.execute(
                "SELECT id, admission_number, full_name, class_level FROM students WHERE id = ? AND status = 'active'",
                (student_id,),
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
                "SELECT 1 FROM grades WHERE student_id = ? AND academic_year = ? AND term = ? LIMIT 1",
                (student_id, academic_year, term),
            ).fetchone()
            if not has_grades:
                flash("A report card can only be published after grades have been recorded for that term.", "danger")
                return redirect(url_for("report_cards", student_id=student_id, academic_year=academic_year, term=term))
            if action not in {"publish", "unpublish"}:
                flash("Unknown report-card action.", "danger")
                return redirect(url_for("report_cards"))
            published = 1 if action == "publish" else 0
            connection.execute(
                """INSERT INTO report_cards (student_id, academic_year, term, published, published_by, published_at)
                   VALUES (?, ?, ?, ?, ?, CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END)
                   ON CONFLICT(student_id, academic_year, term) DO UPDATE SET
                     published = excluded.published, published_by = excluded.published_by,
                     published_at = CASE WHEN excluded.published = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
                     updated_at = CURRENT_TIMESTAMP""",
                (student_id, academic_year, term, published, current_user.id, published),
            )
            connection.commit()
            write_audit(current_user.id, "report_card_" + action, selected_student["admission_number"],
                        academic_year + " / " + term)
            flash("Report card publication updated.", "success")
            return redirect(url_for("report_cards", student_id=student_id, academic_year=academic_year, term=term))

        if selected_student:
            raw_grades = connection.execute(
                """SELECT subject, score, max_score, assessment FROM grades
                   WHERE student_id = ? AND academic_year = ? AND term = ?
                   ORDER BY subject COLLATE NOCASE, assessment COLLATE NOCASE""",
                (student_id, academic_year, term),
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
                "SELECT published, published_at FROM report_cards WHERE student_id = ? AND academic_year = ? AND term = ?",
                (student_id, academic_year, term),
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
    if current_user.role not in ("super_admin", "registrar", "teacher"):
        flash("You are not authorized to register students.", "danger")
        return redirect(url_for("dashboard"))

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
        if any(ord(ch) < 32 or ord(ch) == 127 for value in (full_name, class_level, guardian_name)
               for ch in value):
            flash("Names and class fields contain invalid control characters.", "danger")
            return redirect(url_for("register_student"))
        if guardian_phone and any(ch not in "0123456789+()- ." for ch in guardian_phone):
            flash("Enter a valid guardian phone number.", "danger")
            return redirect(url_for("register_student"))
        if date_of_birth:
            try:
                parsed_dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
                if parsed_dob.isoformat() != date_of_birth or parsed_dob > datetime.now().date():
                    raise ValueError
            except (ValueError, TypeError):
                flash("Enter a valid date of birth that is not in the future.", "danger")
                return redirect(url_for("register_student"))

        connection = get_db_connection()
        try:
            cursor = connection.execute(
                """
                INSERT INTO students (
                    admission_number, full_name, class_level, date_of_birth,
                    guardian_name, guardian_phone, enrollment_date, previous_school,
                    emergency_contact_name, emergency_contact_phone, record_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (admission_number, full_name, class_level, date_of_birth or None,
                 guardian_name or None, guardian_phone or None, enrollment_date or None,
                 previous_school or None, emergency_contact_name or None,
                 emergency_contact_phone or None, record_notes or None),
            )
            student_id_number = f"CMHS-{datetime.now().year}-{cursor.lastrowid:05d}"
            connection.execute(
                "UPDATE students SET student_id_number = ? WHERE id = ?",
                (student_id_number, cursor.lastrowid),
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
        student = connection.execute(
            "SELECT * FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        if student is None:
            abort(404)
        finance_summary = None
        if current_user.role in {"super_admin", "principal", "registrar", "finance_admin"}:
            finance_summary = connection.execute(
                """SELECT COALESCE(total_fee, 0) AS total_fee,
                          COALESCE(amount_paid, 0) AS amount_paid,
                          COALESCE(total_fee, 0) - COALESCE(amount_paid, 0) AS balance
                   FROM student_finances WHERE student_id = ? AND academic_year = ?""",
                (student_id, f"{datetime.now().year}-{datetime.now().year + 1}"),
            ).fetchone()
        attendance = connection.execute(
            """SELECT status, COUNT(*) AS total FROM attendance
               WHERE student_id = ? GROUP BY status""", (student_id,)
        ).fetchall()
        recent_grades = connection.execute(
            """SELECT subject, assessment, score, max_score, academic_year, term, created_at
               FROM grades WHERE student_id = ?
               ORDER BY created_at DESC, id DESC LIMIT 12""", (student_id,)
        ).fetchall()
        recent_attendance = connection.execute(
            """SELECT attendance_date, status, note FROM attendance
               WHERE student_id = ? ORDER BY attendance_date DESC LIMIT 10""", (student_id,)
        ).fetchall()
    finally:
        connection.close()
    return render_template("student_detail.html", student=student, finance_summary=finance_summary,
                           attendance=attendance, recent_grades=recent_grades,
                           recent_attendance=recent_attendance)


@app.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@login_required
def edit_student(student_id):
    if current_user.role not in {"super_admin", "principal", "registrar"}:
        abort(403)
    connection = get_db_connection()
    try:
        student = connection.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
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
            for date_field in ("date_of_birth", "enrollment_date"):
                if fields[date_field]:
                    try:
                        datetime.strptime(fields[date_field], "%Y-%m-%d")
                    except ValueError:
                        flash("Dates must use the valid YYYY-MM-DD format.", "danger")
                        return redirect(url_for("edit_student", student_id=student_id))
            old_class = student["class_level"]
            connection.execute(
                """UPDATE students SET full_name=?, class_level=?, date_of_birth=?,
                   guardian_name=?, guardian_phone=?, enrollment_date=?, previous_school=?,
                   emergency_contact_name=?, emergency_contact_phone=?, record_notes=?
                   WHERE id=?""",
                (fields["full_name"], fields["class_level"], fields["date_of_birth"] or None,
                 fields["guardian_name"] or None, fields["guardian_phone"] or None,
                 fields["enrollment_date"] or None, fields["previous_school"] or None,
                 fields["emergency_contact_name"] or None, fields["emergency_contact_phone"] or None,
                 fields["record_notes"] or None, student_id),
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
            "NULL AS date_of_birth, NULL AS guardian_name, NULL AS guardian_phone"
            if current_user.role == "teacher"
            else "s.date_of_birth, s.guardian_name, s.guardian_phone"
        )
        students = connection.execute(
            f"""
            SELECT s.id, s.student_id_number, s.admission_number, s.full_name, s.class_level,
                   {private_student_fields}, s.status,
                   COALESCE(f.total_fee, 0) AS total_fee,
                   COALESCE(f.amount_paid, 0) AS amount_paid,
                   COALESCE(f.total_fee, 0) - COALESCE(f.amount_paid, 0) AS balance
            FROM students s
            LEFT JOIN student_finances f ON f.student_id = s.id
                AND f.academic_year = ?
            WHERE (? = '' OR s.class_level = ?)
              AND (? = '' OR s.full_name LIKE ? OR s.admission_number LIKE ?
                   OR COALESCE(s.student_id_number, '') LIKE ?)
            ORDER BY s.class_level, s.full_name COLLATE NOCASE
            """,
            (f"{datetime.now().year}-{datetime.now().year + 1}", class_filter, class_filter,
             search_query, f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"),
        ).fetchall()
        classes = connection.execute(
            "SELECT DISTINCT class_level FROM students ORDER BY class_level COLLATE NOCASE"
        ).fetchall()
    finally:
        connection.close()
    return render_template("students.html", students=students, classes=classes,
                           class_filter=class_filter, search_query=search_query,
                           can_view_finances=current_user.role in {"super_admin", "principal", "registrar", "finance_admin"})


@app.route("/student-finances", methods=["GET", "POST"])
@login_required
def student_finances():
    if current_user.role not in {"super_admin", "principal", "registrar", "finance_admin"}:
        abort(403)

    connection = get_db_connection()
    try:
        if request.method == "POST":
            student_id = request.form.get("student_id", "").strip()
            academic_year = request.form.get("academic_year", "").strip()
            total_text = request.form.get("total_fee", "").strip()
            paid_text = request.form.get("amount_paid", "").strip()
            notes = request.form.get("notes", "").strip()
            valid_year = (len(academic_year) == 9 and academic_year[4] == "-"
                          and academic_year[:4].isdigit() and academic_year[5:].isdigit()
                          and int(academic_year[5:]) == int(academic_year[:4]) + 1)
            student = connection.execute(
                "SELECT id, admission_number FROM students WHERE id = ? AND status = 'active'",
                (student_id,),
            ).fetchone()
            try:
                total_fee, amount_paid = float(total_text), float(paid_text)
                if not (0 <= amount_paid <= total_fee and total_fee <= 100000000
                        and len(notes) <= 250 and valid_year and student):
                    raise ValueError
            except (ValueError, TypeError):
                flash("Check the student, academic year, fee and payment amounts. Payments cannot exceed the total fees.", "danger")
                return redirect(url_for("student_finances"))
            connection.execute(
                """INSERT INTO student_finances
                   (student_id, academic_year, total_fee, amount_paid, notes, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(student_id, academic_year) DO UPDATE SET
                     total_fee = excluded.total_fee, amount_paid = excluded.amount_paid,
                     notes = excluded.notes, updated_by = excluded.updated_by,
                     updated_at = CURRENT_TIMESTAMP""",
                (student_id, academic_year, total_fee, amount_paid, notes or None, current_user.id),
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
            "WHERE status = 'active' ORDER BY class_level, full_name"
        ).fetchall()
        records = connection.execute(
            """SELECT s.student_id_number, s.admission_number, s.full_name, s.class_level,
                      f.total_fee, f.amount_paid, f.total_fee - f.amount_paid AS balance,
                      f.updated_at, f.notes
               FROM student_finances f JOIN students s ON s.id = f.student_id
               WHERE f.academic_year = ? ORDER BY s.class_level, s.full_name COLLATE NOCASE""",
            (academic_year,),
        ).fetchall()
        summary = connection.execute(
            """SELECT COALESCE(SUM(total_fee), 0) AS total_fees,
                      COALESCE(SUM(amount_paid), 0) AS total_paid,
                      COALESCE(SUM(total_fee - amount_paid), 0) AS total_balance
               FROM student_finances WHERE academic_year = ?""",
            (academic_year,),
        ).fetchone()
    finally:
        connection.close()
    return render_template("student_finances.html", students=students, records=records,
                           academic_year=academic_year, summary=summary)


if __name__ == "__main__":
    app.run(port=5000, debug=False)
