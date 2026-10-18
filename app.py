import os
import sqlite3
from pathlib import Path

from flask import Flask, render_template, redirect, url_for, request, flash
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

# Set DATABASE_PATH on the hosting provider to use a persistent disk, e.g.
# /var/data/glihs.db on a Render disk mounted at /var/data.
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


# --------------------------------------------------
# 2. DATABASE CONNECTION AND INITIALIZATION
# --------------------------------------------------

def get_db_connection():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    """Create missing tables without deleting or resetting existing records."""
    connection = get_db_connection()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK (role IN ('student', 'teacher', 'admin'))
            )
            """
        )
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

        # Optional first-admin setup. Configure both variables in the hosting
        # environment. The password is hashed; it is never stored as plain text.
        initial_username = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
        initial_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
        if initial_username and initial_password:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (username, password_hash, role)
                VALUES (?, ?, 'admin')
                """,
                (initial_username, generate_password_hash(initial_password)),
            )
        connection.commit()
    finally:
        connection.close()


initialize_database()


# --------------------------------------------------
# 3. USER MODEL AND DATABASE LOOKUPS
# --------------------------------------------------

class User(UserMixin):
    def __init__(self, user_id, username, password_hash, role):
        self.id = str(user_id)
        self.username = username
        self.password_hash = password_hash
        self.role = role


def get_user_by_id(user_id):
    connection = get_db_connection()
    try:
        row = connection.execute(
            "SELECT id, username, password_hash, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return User(row["id"], row["username"], row["password_hash"], row["role"])
    finally:
        connection.close()


def get_user_by_username(username):
    connection = get_db_connection()
    try:
        row = connection.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return None
        return User(row["id"], row["username"], row["password_hash"], row["role"])
    finally:
        connection.close()


@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)


# --------------------------------------------------
# 4. PUBLIC PAGES AND AUTHENTICATION
# --------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


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
    logout_user()
    return redirect(url_for("index"))


# --------------------------------------------------
# 5. STUDENT RECORDS
# --------------------------------------------------

@app.route("/students/register", methods=["GET", "POST"])
@login_required
def register_student():
    if current_user.role not in ("admin", "teacher"):
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
            flash(
                "Admission number, full name, and class are required.",
                "danger",
            )
            return redirect(url_for("register_student"))

        connection = get_db_connection()
        try:
            connection.execute(
                """
                INSERT INTO students (
                    admission_number, full_name, class_level,
                    date_of_birth, guardian_name, guardian_phone
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    admission_number,
                    full_name,
                    class_level,
                    date_of_birth or None,
                    guardian_name or None,
                    guardian_phone or None,
                ),
            )
            connection.commit()
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
    if current_user.role not in ("admin", "teacher"):
        flash("You are not authorized to view student records.", "danger")
        return redirect(url_for("dashboard"))

    connection = get_db_connection()
    try:
        students = connection.execute(
            """
            SELECT id, admission_number, full_name, class_level,
                   date_of_birth, guardian_name, guardian_phone, status
            FROM students
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    return render_template("students.html", students=students)


if __name__ == "__main__":
    app.run(port=5000, debug=False)
