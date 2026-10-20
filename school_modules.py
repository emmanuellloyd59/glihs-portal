"""Version 0.3 school-module foundation.

These routes intentionally remain single-school until the tenant migration has
been implemented and verified across every legacy table and query.
"""
import sqlite3
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required as flask_login_required

bp = Blueprint("school_modules", __name__)
DB = None
login_required = None
current_user = None
write_audit = None

STAFF = {"teacher", "super_admin", "principal", "registrar", "academic_admin"}
ACADEMIC_ADMINS = {"super_admin", "principal", "academic_admin"}
STUDENT_ROLES = {"student"}
ALL_ADMINS = {"super_admin", "principal", "registrar", "academic_admin", "finance_admin"}


def _role_allowed(roles):
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in roles:
        abort(403)


def _init_tables():
    conn = DB()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS timetable_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_name TEXT NOT NULL,
                subject TEXT NOT NULL,
                teacher_name TEXT NOT NULL,
                room TEXT NOT NULL DEFAULT '',
                day_of_week TEXT NOT NULL CHECK(day_of_week IN
                    ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')),
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_timetable_day_class
                ON timetable_entries(day_of_week, class_name);
            CREATE TABLE IF NOT EXISTS homework_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                class_name TEXT NOT NULL,
                subject TEXT NOT NULL,
                instructions TEXT NOT NULL,
                due_date TEXT NOT NULL,
                resource_url TEXT NOT NULL DEFAULT '',
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_homework_due ON homework_assignments(due_date);
            CREATE TABLE IF NOT EXISTS homework_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL REFERENCES homework_assignments(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                submission_text TEXT NOT NULL,
                submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                feedback TEXT,
                reviewed_by INTEGER REFERENCES users(id),
                reviewed_at TEXT,
                UNIQUE(assignment_id, student_id)
            );
            CREATE TABLE IF NOT EXISTS staff_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                job_title TEXT NOT NULL DEFAULT '',
                subjects TEXT NOT NULL DEFAULT '',
                assigned_classes TEXT NOT NULL DEFAULT '',
                work_phone TEXT NOT NULL DEFAULT '',
                updated_by INTEGER NOT NULL REFERENCES users(id),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS school_announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                audience TEXT NOT NULL DEFAULT 'everyone'
                    CHECK(audience IN ('everyone','students','staff','parents')),
                published INTEGER NOT NULL DEFAULT 1 CHECK(published IN (0,1)),
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_announcements_published
                ON school_announcements(published, created_at);
            CREATE TABLE IF NOT EXISTS parent_student_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guardian_user_id INTEGER NOT NULL REFERENCES users(id),
                student_id INTEGER NOT NULL REFERENCES students(id),
                verified_by INTEGER NOT NULL REFERENCES users(id),
                verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guardian_user_id, student_id)
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _clean(field, max_length, required=True):
    value = request.form.get(field, "").strip()
    if required and not value:
        raise ValueError("Please complete all required fields.")
    if len(value) > max_length:
        raise ValueError(f"{field.replace('_', ' ').title()} is too long.")
    return value


@bp.route("/school-tools", methods=["GET", "POST"])
@flask_login_required
def school_tools():
    _role_allowed(STAFF | {"finance_admin"} | STUDENT_ROLES)
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        try:
            conn = DB()
            try:
                if action == "add_timetable":
                    _role_allowed(ACADEMIC_ADMINS)
                    class_name = _clean("class_name", 80)
                    subject = _clean("subject", 100)
                    teacher = _clean("teacher_name", 120)
                    room = _clean("room", 80, required=False)
                    day = _clean("day_of_week", 12)
                    start = _clean("start_time", 5)
                    end = _clean("end_time", 5)
                    if day not in {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}:
                        raise ValueError("Choose a valid school day.")
                    try:
                        start_minutes = int(start[:2]) * 60 + int(start[3:])
                        end_minutes = int(end[:2]) * 60 + int(end[3:])
                        if len(start) != 5 or len(end) != 5 or start[2] != ":" or end[2] != ":":
                            raise ValueError
                        if not (0 <= start_minutes < 1440 and 0 < end_minutes <= 1440 and start_minutes < end_minutes):
                            raise ValueError
                    except (ValueError, IndexError):
                        raise ValueError("Enter valid 24-hour start and end times; end must be after start.")
                    clash = conn.execute("""
                        SELECT 1 FROM timetable_entries
                        WHERE day_of_week = ? AND start_time < ? AND end_time > ?
                          AND (class_name = ? OR teacher_name = ? OR (room <> '' AND room = ?))
                        LIMIT 1
                    """, (day, end, start, class_name, teacher, room)).fetchone()
                    if clash:
                        raise ValueError("Timetable conflict: that class, teacher, or room is already booked during this period.")
                    conn.execute("""INSERT INTO timetable_entries
                        (class_name, subject, teacher_name, room, day_of_week, start_time, end_time, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (class_name, subject, teacher, room, day, start, end, int(current_user.id)))
                    conn.commit()
                    write_audit(current_user.id, "timetable_entry_created", current_user.username,
                                f"{class_name}; {subject}; {day} {start}-{end}")
                    flash("Timetable entry saved.", "success")
                elif action == "add_homework":
                    _role_allowed(STAFF)
                    title = _clean("title", 160)
                    class_name = _clean("class_name", 80)
                    subject = _clean("subject", 100)
                    instructions = _clean("instructions", 4000)
                    due = _clean("due_date", 10)
                    resource = _clean("resource_url", 500, required=False)
                    try:
                        due_date = date.fromisoformat(due)
                    except ValueError:
                        raise ValueError("Choose a valid homework due date.")
                    if resource and not (resource.startswith("https://") or resource.startswith("http://")):
                        raise ValueError("Resource links must begin with http:// or https://.")
                    conn.execute("""INSERT INTO homework_assignments
                        (title, class_name, subject, instructions, due_date, resource_url, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (title, class_name, subject, instructions, due_date.isoformat(), resource, int(current_user.id)))
                    conn.commit()
                    write_audit(current_user.id, "homework_created", current_user.username, f"{class_name}; {title}")
                    flash("Homework assignment published to the school workspace.", "success")
                elif action == "submit_homework":
                    _role_allowed(STUDENT_ROLES)
                    assignment_id = request.form.get("assignment_id", type=int)
                    submission_text = _clean("submission_text", 5000)
                    if not assignment_id:
                        raise ValueError("Choose a valid homework assignment.")
                    student = conn.execute(
                        "SELECT id, class_level FROM students WHERE account_user_id = ? AND status = 'active'",
                        (int(current_user.id),),
                    ).fetchone()
                    if not student:
                        abort(403)
                    assignment = conn.execute(
                        "SELECT id, class_name, due_date FROM homework_assignments WHERE id = ?",
                        (assignment_id,),
                    ).fetchone()
                    if not assignment or assignment["class_name"].strip().casefold() != student["class_level"].strip().casefold():
                        abort(403)
                    if assignment["due_date"] < date.today().isoformat():
                        raise ValueError("This homework deadline has passed; contact your teacher.")
                    conn.execute("""
                        INSERT INTO homework_submissions (assignment_id, student_id, submission_text)
                        VALUES (?, ?, ?)
                        ON CONFLICT(assignment_id, student_id) DO UPDATE SET
                            submission_text=excluded.submission_text,
                            submitted_at=CURRENT_TIMESTAMP,
                            feedback=NULL, reviewed_by=NULL, reviewed_at=NULL
                    """, (assignment_id, student["id"], submission_text))
                    conn.commit()
                    write_audit(current_user.id, "homework_submitted", current_user.username,
                                f"Assignment ID: {assignment_id}")
                    flash("Homework submitted successfully.", "success")
                elif action == "review_submission":
                    _role_allowed(STAFF | {"finance_admin"})
                    submission_id = request.form.get("submission_id", type=int)
                    feedback = _clean("feedback", 2000)
                    submission = conn.execute("""
                        SELECT s.id, h.created_by FROM homework_submissions s
                        JOIN homework_assignments h ON h.id = s.assignment_id WHERE s.id = ?
                    """, (submission_id,)).fetchone()
                    if not submission:
                        raise ValueError("Submission not found.")
                    if current_user.role not in ACADEMIC_ADMINS and int(submission["created_by"]) != int(current_user.id):
                        abort(403)
                    conn.execute("""UPDATE homework_submissions SET feedback=?, reviewed_by=?,
                        reviewed_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (feedback, int(current_user.id), submission_id))
                    conn.commit()
                    write_audit(current_user.id, "homework_feedback_saved", current_user.username,
                                f"Submission ID: {submission_id}")
                    flash("Feedback saved.", "success")
                elif action == "save_staff_profile":
                    _role_allowed(ALL_ADMINS)
                    user_id = request.form.get("staff_user_id", type=int)
                    job_title = _clean("job_title", 120)
                    subjects = _clean("staff_subjects", 500, required=False)
                    assigned_classes = _clean("assigned_classes", 500, required=False)
                    work_phone = _clean("work_phone", 40, required=False)
                    staff_user = conn.execute("""SELECT id, username, role FROM users
                        WHERE id=? AND role IN ('teacher','principal','registrar','academic_admin','finance_admin')""",
                        (user_id,)).fetchone()
                    if not staff_user:
                        raise ValueError("Select a valid staff account.")
                    conn.execute("""INSERT INTO staff_profiles
                        (user_id, job_title, subjects, assigned_classes, work_phone, updated_by)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET job_title=excluded.job_title,
                        subjects=excluded.subjects, assigned_classes=excluded.assigned_classes,
                        work_phone=excluded.work_phone, updated_by=excluded.updated_by,
                        updated_at=CURRENT_TIMESTAMP""",
                        (user_id, job_title, subjects, assigned_classes, work_phone, int(current_user.id)))
                    conn.commit()
                    write_audit(current_user.id, "staff_profile_updated", staff_user["username"], job_title)
                    flash("Staff profile saved.", "success")
                elif action == "add_announcement":
                    _role_allowed(ACADEMIC_ADMINS)
                    title = _clean("announcement_title", 160)
                    body = _clean("announcement_body", 4000)
                    audience = _clean("audience", 20)
                    if audience not in {"everyone", "students", "staff", "parents"}:
                        raise ValueError("Choose a valid announcement audience.")
                    conn.execute("""INSERT INTO school_announcements
                        (title, body, audience, created_by) VALUES (?, ?, ?, ?)""",
                        (title, body, audience, int(current_user.id)))
                    conn.commit()
                    write_audit(current_user.id, "announcement_published", current_user.username, title)
                    flash("Announcement published.", "success")
                elif action == "delete_timetable":
                    _role_allowed(ACADEMIC_ADMINS)
                    entry_id = request.form.get("entry_id", type=int)
                    if not entry_id:
                        raise ValueError("Select a valid timetable entry.")
                    conn.execute("DELETE FROM timetable_entries WHERE id = ?", (entry_id,))
                    conn.commit()
                    write_audit(current_user.id, "timetable_entry_deleted", current_user.username, str(entry_id))
                    flash("Timetable entry removed.", "success")
                else:
                    abort(400)
            finally:
                conn.close()
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("school_modules.school_tools"))

    conn = DB()
    try:
        if current_user.role == "student":
            student = conn.execute("SELECT id, class_level FROM students WHERE account_user_id = ? AND status = 'active'",
                                   (int(current_user.id),)).fetchone()
            # Students must only see the timetable for their own active class.
            # A missing/unlinked student record intentionally returns no entries.
            timetable = conn.execute("""SELECT * FROM timetable_entries
                WHERE ? IS NOT NULL AND lower(trim(class_name)) = lower(trim(?))
                ORDER BY CASE day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END,
                start_time, class_name""",
                (student["id"] if student else None, student["class_level"] if student else "")).fetchall()
            homework = conn.execute("""SELECT h.*, u.username AS author,
                s.submission_text, s.submitted_at, s.feedback, s.reviewed_at
                FROM homework_assignments h JOIN users u ON u.id = h.created_by
                LEFT JOIN homework_submissions s ON s.assignment_id = h.id AND s.student_id = ?
                WHERE ? IS NOT NULL AND lower(trim(h.class_name)) = lower(trim(?))
                ORDER BY h.due_date, h.id DESC LIMIT 100""",
                (student["id"] if student else None, student["id"] if student else None,
                 student["class_level"] if student else "")).fetchall()
        else:
            timetable = conn.execute("""SELECT * FROM timetable_entries
                ORDER BY CASE day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END,
                start_time, class_name""").fetchall()
            homework = conn.execute("""SELECT h.*, u.username AS author FROM homework_assignments h
                JOIN users u ON u.id = h.created_by ORDER BY h.due_date, h.id DESC LIMIT 100""").fetchall()
        if current_user.role in ALL_ADMINS:
            staff_profiles = conn.execute("""SELECT u.id AS user_id, u.username, u.full_name, u.role,
                u.status, p.job_title, p.subjects, p.assigned_classes, p.work_phone
                FROM users u LEFT JOIN staff_profiles p ON p.user_id=u.id
                WHERE u.role IN ('teacher','principal','registrar','academic_admin','finance_admin')
                ORDER BY u.role, u.full_name LIMIT 200""").fetchall()
        else:
            staff_profiles = []
        if current_user.role in ACADEMIC_ADMINS:
            announcements = conn.execute("""SELECT a.*, u.username AS author FROM school_announcements a
                JOIN users u ON u.id = a.created_by ORDER BY a.created_at DESC LIMIT 100""").fetchall()
        elif current_user.role == "student":
            announcements = conn.execute("""SELECT a.*, u.username AS author FROM school_announcements a
                JOIN users u ON u.id = a.created_by WHERE a.published = 1
                AND a.audience IN ('everyone','students') ORDER BY a.created_at DESC LIMIT 100""").fetchall()
        else:
            announcements = conn.execute("""SELECT a.*, u.username AS author FROM school_announcements a
                JOIN users u ON u.id = a.created_by WHERE a.published = 1
                AND a.audience IN ('everyone','staff') ORDER BY a.created_at DESC LIMIT 100""").fetchall()
        stats = {
            "timetable": conn.execute("SELECT COUNT(*) FROM timetable_entries").fetchone()[0],
            "homework": conn.execute("SELECT COUNT(*) FROM homework_assignments").fetchone()[0],
            "announcements": conn.execute("SELECT COUNT(*) FROM school_announcements WHERE published = 1").fetchone()[0],
            "students": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "submissions": conn.execute("SELECT COUNT(*) FROM homework_submissions").fetchone()[0],
            "staff": conn.execute("SELECT COUNT(*) FROM users WHERE role IN ('teacher','principal','registrar','academic_admin','finance_admin') AND status='active'").fetchone()[0],
        }
        staff_rows = conn.execute("""SELECT username, full_name, role, status FROM users
            WHERE role IN ('teacher','principal','registrar','academic_admin','finance_admin')
            ORDER BY role, full_name LIMIT 200""").fetchall() if current_user.role in ALL_ADMINS else []
    finally:
        conn.close()
    submissions = []
    if current_user.role in STAFF | {"finance_admin"}:
        conn = DB()
        try:
            submissions = conn.execute("""SELECT s.id, s.submission_text, s.submitted_at, s.feedback,
                s.reviewed_at, st.full_name AS student_name, st.class_level, h.title, h.subject,
                h.created_by FROM homework_submissions s
                JOIN students st ON st.id=s.student_id
                JOIN homework_assignments h ON h.id=s.assignment_id
                ORDER BY s.submitted_at DESC LIMIT 100""").fetchall()
        finally:
            conn.close()
    return render_template("school_tools.html", timetable=timetable, homework=homework,
                           announcements=announcements, stats=stats, staff_rows=staff_rows,
                           staff_profiles=staff_profiles, submissions=submissions,
                           is_student=current_user.role == "student",
                           student_linked=bool(current_user.role == "student" and student),
                           can_manage_timetable=current_user.role in ACADEMIC_ADMINS,
                           can_manage_staff=current_user.role in ALL_ADMINS,
                           can_publish=current_user.role in ACADEMIC_ADMINS,
                           can_assign_homework=current_user.role in STAFF)


def register_school_modules(app, db_connection, login_required_fn, user_proxy, audit_fn):
    global DB, login_required, current_user, write_audit
    DB, login_required, current_user, write_audit = (
        db_connection, login_required_fn, user_proxy, audit_fn
    )
    _init_tables()
    app.register_blueprint(bp)
