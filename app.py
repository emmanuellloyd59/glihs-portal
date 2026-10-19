import os
import secrets
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, render_template, redirect, url_for, request, flash, abort
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

CSRFProtect(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."

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


def initialize_database():
    """Create or upgrade tables without intentionally resetting existing records."""
    connection = get_db_connection()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
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
                UNIQUE(student_id, subject, assessment)
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
    def __init__(self, user_id, username, password_hash, role, is_super_admin=False):
        self.id = str(user_id)
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.is_super_admin = bool(is_super_admin)


def get_user_by_id(user_id):
    connection = get_db_connection()
    try:
        row = connection.execute(
            """
            SELECT id, username, password_hash, role, is_super_admin
            FROM users WHERE id = ? AND status = 'active'
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return User(row["id"], row["username"], row["password_hash"],
                    row["role"], row["is_super_admin"])
    finally:
        connection.close()


def get_user_by_username(username):
    connection = get_db_connection()
    try:
        row = connection.execute(
            """
            SELECT id, username, password_hash, role, is_super_admin
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
            login_user(user)
            write_audit(user.id, "login_success", user.username)
            return redirect(url_for("dashboard"))
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
            elif action == "change_role":
                new_role = request.form.get("new_role", "").strip()
                if target["is_super_admin"] or new_role not in STAFF_ROLES:
                    flash("Super Administrator roles cannot be changed here.", "danger")
                else:
                    connection.execute(
                        "UPDATE users SET role = ? WHERE id = ?",
                        (new_role, target_id_int),
                    )
                    connection.execute(
                        """
                        INSERT INTO audit_log (actor_user_id, action, target_username, details)
                        VALUES (?, 'staff_role_changed', ?, ?)
                        """,
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
            SELECT id, username, full_name, role, status, is_super_admin, created_at, approved_at
            FROM users ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                username COLLATE NOCASE
            """
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
        audit_entries=audit_entries,
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
                score_text = request.form.get("score", "").strip()
                max_text = request.form.get("max_score", "").strip()
                if not subject or len(subject) > 80 or not assessment or len(assessment) > 100:
                    flash("Enter a subject and assessment name within the stated limits.", "danger")
                    return redirect(url_for("academics"))
                try:
                    score, max_score = float(score_text), float(max_text)
                    if not (0 <= score <= 100 and 0 < max_score <= 100 and score <= max_score):
                        raise ValueError
                except (ValueError, TypeError):
                    flash("Scores must be valid numbers: score 0–100, maximum 1–100, and score cannot exceed maximum.", "danger")
                    return redirect(url_for("academics"))
                try:
                    connection.execute(
                        """INSERT INTO grades (student_id, subject, assessment, score, max_score, recorded_by)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (student_id, subject, assessment, score, max_score, current_user.id),
                    )
                    connection.commit()
                    write_audit(current_user.id, "grade_recorded", str(student_id),
                                f"{subject} / {assessment}: {score}/{max_score}")
                    flash("Grade saved successfully.", "success")
                except sqlite3.IntegrityError:
                    flash("A grade for this student, subject, and assessment already exists. Use a different assessment name.", "danger")
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
                      g.assessment, g.score, g.max_score, u.username AS recorded_by, g.created_at
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
# 6. STUDENT RECORDS
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

        if not admission_number or not full_name or not class_level:
            flash("Admission number, full name, and class are required.", "danger")
            return redirect(url_for("register_student"))

        connection = get_db_connection()
        try:
            connection.execute(
                """
                INSERT INTO students (
                    admission_number, full_name, class_level,
                    date_of_birth, guardian_name, guardian_phone
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (admission_number, full_name, class_level,
                 date_of_birth or None, guardian_name or None, guardian_phone or None),
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


@app.route("/students")
@login_required
def list_students():
    if current_user.role not in STUDENT_RECORD_ROLES:
        flash("You are not authorized to view student records.", "danger")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    try:
        students = connection.execute(
            """
            SELECT id, admission_number, full_name, class_level,
                   date_of_birth, guardian_name, guardian_phone, status
            FROM students ORDER BY id DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return render_template("students.html", students=students)


if __name__ == "__main__":
    app.run(port=5000, debug=False)
