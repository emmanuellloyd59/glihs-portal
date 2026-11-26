"""School-scoped staff HR, attendance, leave, recovery and permission services.

The schema helper is called from the main additive migration transaction. Routes are
registered separately so the module does not import the Flask application.
"""
import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


STAFF_HR_ROLES = {
    "school_admin", "principal", "vice_principal", "registrar",
    "admissions_officer", "academic_admin", "attendance_officer",
    "finance_admin", "librarian", "counselor", "teacher",
}

STAFF_PERMISSION_MAXIMUMS = {
    "staff.directory.view": {"super_admin", "school_admin", "principal", "vice_principal"},
    "staff.account.manage": {"super_admin", "school_admin", "principal", "vice_principal"},
    "staff.employment.manage": {"super_admin", "school_admin"},
    "staff.structure.manage": {"super_admin", "school_admin"},
    "staff.assignments.manage": {"super_admin", "school_admin", "principal", "vice_principal"},
    "staff.attendance.view_all": {"super_admin", "school_admin", "principal", "vice_principal", "attendance_officer"},
    "staff.attendance.manage": {"super_admin", "school_admin", "principal", "vice_principal", "attendance_officer"},
    "staff.leave.approve": {"super_admin", "school_admin", "principal", "vice_principal"},
    "staff.activity.view": {"super_admin", "school_admin", "principal", "vice_principal"},
    "staff.permissions.manage": {"super_admin", "school_admin"},
    "staff.password.reset": {"super_admin", "school_admin"},
    "staff.profile.self_edit": STAFF_HR_ROLES,
    "staff.attendance.self": STAFF_HR_ROLES,
    "staff.leave.request": STAFF_HR_ROLES,
}

STAFF_PERMISSION_LABELS = {
    "staff.directory.view": "View staff directory",
    "staff.account.manage": "Create, activate and suspend staff accounts",
    "staff.employment.manage": "Manage employment records",
    "staff.structure.manage": "Manage departments and positions",
    "staff.assignments.manage": "Manage teacher class and subject assignments",
    "staff.attendance.view_all": "View staff attendance records",
    "staff.attendance.manage": "Record and correct staff attendance",
    "staff.leave.approve": "Review and decide staff leave requests",
    "staff.activity.view": "View staff activity history",
    "staff.permissions.manage": "Manage school staff permissions",
    "staff.password.reset": "Issue staff password recovery codes",
    "staff.profile.self_edit": "Edit permitted personal profile fields",
    "staff.attendance.self": "Record own staff attendance",
    "staff.leave.request": "Submit and track own leave requests",
}


def _platform_actor_sql(user_id_sql):
    return (
        f"COALESCE((SELECT is_super_admin FROM users WHERE id={user_id_sql}),0)=1 "
        f"AND (SELECT role FROM users WHERE id={user_id_sql})='super_admin' "
        f"AND (SELECT school_id FROM users WHERE id={user_id_sql}) IS NULL"
    )


def ensure_staff_hr_schema(connection):
    """Create the Prompt 06 schema additively inside the caller's transaction."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(school_id,name)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            rank_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(school_id,name)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
            employee_number TEXT,
            phone TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            department_id INTEGER REFERENCES staff_departments(id),
            position_id INTEGER REFERENCES staff_positions(id),
            employment_type TEXT NOT NULL DEFAULT 'full_time'
                CHECK (employment_type IN ('full_time','part_time','contract','volunteer')),
            employment_status TEXT NOT NULL DEFAULT 'active'
                CHECK (employment_status IN ('active','on_leave','terminated')),
            hire_date TEXT,
            contract_start_date TEXT,
            contract_end_date TEXT,
            verification_status TEXT NOT NULL DEFAULT 'legacy'
                CHECK (verification_status IN ('pending','verified','legacy','rejected')),
            verified_by INTEGER REFERENCES users(id),
            verified_at TEXT,
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (contract_start_date IS NULL OR contract_end_date IS NULL
                   OR contract_end_date >= contract_start_date)
        )
    """)
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_employee_number
        ON staff_profiles(school_id,employee_number)
        WHERE employee_number IS NOT NULL AND employee_number <> ''
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_staff_profiles_school
        ON staff_profiles(school_id,employment_status,department_id,position_id)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_employment_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            event_type TEXT NOT NULL CHECK (event_type IN (
                'hired','profile_updated','department_changed','position_changed',
                'employment_status_changed','employment_type_changed','role_changed',
                'account_suspended','account_reactivated','verification_completed'
            )),
            old_value TEXT,
            new_value TEXT,
            notes TEXT NOT NULL DEFAULT '',
            effective_date TEXT NOT NULL,
            recorded_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_staff_employment_history
        ON staff_employment_records(school_id,user_id,created_at DESC)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('present','absent','late','excused','remote')),
            check_in_at TEXT,
            check_out_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            recorded_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(school_id,user_id,attendance_date),
            CHECK (check_out_at IS NULL OR check_in_at IS NOT NULL),
            CHECK (check_out_at IS NULL OR check_out_at >= check_in_at)
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_staff_attendance_school_date
        ON staff_attendance(school_id,attendance_date,status)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            leave_type TEXT NOT NULL CHECK (leave_type IN (
                'annual','sick','maternity','paternity','bereavement','unpaid','other'
            )),
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected','cancelled')),
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT,
            reviewer_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (end_date >= start_date),
            CHECK (reviewed_by IS NULL OR reviewed_by <> user_id)
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_staff_leave_school_status
        ON staff_leave_requests(school_id,status,start_date,end_date)
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_staff_leave_user
        ON staff_leave_requests(school_id,user_id,requested_at DESC)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_password_reset_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','issued','rejected','completed')),
            token_hash TEXT,
            token_expires_at TEXT,
            token_used_at TEXT,
            reviewed_by INTEGER REFERENCES users(id),
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_reset_one_open_request
        ON staff_password_reset_requests(school_id,user_id)
        WHERE status IN ('pending','issued')
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_staff_reset_review
        ON staff_password_reset_requests(school_id,status,requested_at)
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staff_password_reset_rate_limits (
            ip_hash TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            window_started_at TEXT NOT NULL,
            blocked_until TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS school_role_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            role TEXT NOT NULL,
            permission_key TEXT NOT NULL,
            enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
            updated_by INTEGER REFERENCES users(id),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(school_id,role,permission_key)
        )
    """)

    # Existing active staff are retained and explicitly marked as legacy until
    # an administrator has a chance to verify/update their HR profile.
    connection.execute("""
        INSERT OR IGNORE INTO staff_profiles
            (school_id,user_id,verification_status,verified_by,verified_at)
        SELECT school_id,id,
               CASE WHEN approved_by IS NOT NULL THEN 'verified' ELSE 'legacy' END,
               approved_by,approved_at
        FROM users
        WHERE school_id IS NOT NULL
          AND role NOT IN ('super_admin','student','parent')
    """)

    # Seed default permissions for every existing school. Missing override rows
    # still fall back to this same policy for schools created after startup.
    for school in connection.execute("SELECT id FROM schools").fetchall():
        school_id = school["id"]
        for permission_key, roles in STAFF_PERMISSION_MAXIMUMS.items():
            for role in roles:
                if role == "super_admin":
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO school_role_permissions
                       (school_id,role,permission_key,enabled)
                       VALUES (?,?,?,1)""",
                    (school_id, role, permission_key),
                )

    triggers = (
        ("trg_staff_user_profile_insert", """
            CREATE TRIGGER trg_staff_user_profile_insert
            AFTER INSERT ON users
            WHEN NEW.school_id IS NOT NULL
             AND NEW.role NOT IN ('super_admin','student','parent')
            BEGIN
                INSERT OR IGNORE INTO staff_profiles
                    (school_id,user_id,verification_status,verified_by,verified_at,updated_by)
                VALUES (
                    NEW.school_id,NEW.id,
                    CASE WHEN NEW.status='pending' THEN 'pending'
                         WHEN NEW.approved_by IS NOT NULL THEN 'verified'
                         ELSE 'legacy' END,
                    NEW.approved_by,NEW.approved_at,NEW.approved_by
                );
            END
        """),
        ("trg_staff_user_profile_approval", """
            CREATE TRIGGER trg_staff_user_profile_approval
            AFTER UPDATE OF status,approved_by,approved_at,role,school_id ON users
            WHEN NEW.school_id IS NOT NULL
             AND NEW.role NOT IN ('super_admin','student','parent')
             AND NEW.status='active'
            BEGIN
                INSERT OR IGNORE INTO staff_profiles
                    (school_id,user_id,verification_status,verified_by,verified_at,updated_by)
                VALUES (NEW.school_id,NEW.id,
                        CASE WHEN NEW.approved_by IS NOT NULL THEN 'verified' ELSE 'legacy' END,
                        NEW.approved_by,NEW.approved_at,NEW.approved_by);
                UPDATE staff_profiles
                   SET school_id=NEW.school_id,
                       verification_status=CASE WHEN NEW.approved_by IS NOT NULL THEN 'verified' ELSE verification_status END,
                       verified_by=COALESCE(NEW.approved_by,verified_by),
                       verified_at=COALESCE(NEW.approved_at,verified_at),
                       updated_by=COALESCE(NEW.approved_by,updated_by),
                       updated_at=CURRENT_TIMESTAMP
                 WHERE user_id=NEW.id;
            END
        """),
        ("trg_staff_profile_insert_guard", f"""
            CREATE TRIGGER trg_staff_profile_insert_guard
            BEFORE INSERT ON staff_profiles
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (SELECT role FROM users WHERE id=NEW.user_id) IN ('super_admin','student','parent')
              OR (NEW.department_id IS NOT NULL AND
                  (SELECT school_id FROM staff_departments WHERE id=NEW.department_id) IS NOT NEW.school_id)
              OR (NEW.position_id IS NOT NULL AND
                  (SELECT school_id FROM staff_positions WHERE id=NEW.position_id) IS NOT NEW.school_id)
              OR (NEW.updated_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.updated_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.updated_by')}))
            BEGIN SELECT RAISE(ABORT,'staff profile school ownership mismatch'); END
        """),
        ("trg_staff_profile_update_guard", f"""
            CREATE TRIGGER trg_staff_profile_update_guard
            BEFORE UPDATE OF school_id,user_id,department_id,position_id,updated_by ON staff_profiles
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (NEW.department_id IS NOT NULL AND
                  (SELECT school_id FROM staff_departments WHERE id=NEW.department_id) IS NOT NEW.school_id)
              OR (NEW.position_id IS NOT NULL AND
                  (SELECT school_id FROM staff_positions WHERE id=NEW.position_id) IS NOT NEW.school_id)
              OR (NEW.updated_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.updated_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.updated_by')}))
            BEGIN SELECT RAISE(ABORT,'staff profile school ownership mismatch'); END
        """),
        ("trg_staff_department_actor", f"""
            CREATE TRIGGER trg_staff_department_actor
            BEFORE INSERT ON staff_departments
            WHEN (NEW.created_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.created_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.created_by')}))
            BEGIN SELECT RAISE(ABORT,'department actor school mismatch'); END
        """),
        ("trg_staff_position_actor", f"""
            CREATE TRIGGER trg_staff_position_actor
            BEFORE INSERT ON staff_positions
            WHEN (NEW.created_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.created_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.created_by')}))
            BEGIN SELECT RAISE(ABORT,'position actor school mismatch'); END
        """),
        ("trg_staff_employment_insert_guard", f"""
            CREATE TRIGGER trg_staff_employment_insert_guard
            BEFORE INSERT ON staff_employment_records
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (NEW.recorded_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.recorded_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.recorded_by')}))
            BEGIN SELECT RAISE(ABORT,'staff employment school mismatch'); END
        """),
        ("trg_staff_employment_no_update", """
            CREATE TRIGGER trg_staff_employment_no_update
            BEFORE UPDATE ON staff_employment_records
            BEGIN SELECT RAISE(ABORT,'staff employment history is append-only'); END
        """),
        ("trg_staff_employment_no_delete", """
            CREATE TRIGGER trg_staff_employment_no_delete
            BEFORE DELETE ON staff_employment_records
            BEGIN SELECT RAISE(ABORT,'staff employment history is append-only'); END
        """),
        ("trg_staff_attendance_insert_guard", f"""
            CREATE TRIGGER trg_staff_attendance_insert_guard
            BEFORE INSERT ON staff_attendance
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (SELECT role FROM users WHERE id=NEW.user_id) IN ('super_admin','student','parent')
              OR (SELECT school_id FROM users WHERE id=NEW.recorded_by) IS NOT NEW.school_id
                 AND NOT ({_platform_actor_sql('NEW.recorded_by')})
            BEGIN SELECT RAISE(ABORT,'staff attendance school mismatch'); END
        """),
        ("trg_staff_attendance_update_guard", f"""
            CREATE TRIGGER trg_staff_attendance_update_guard
            BEFORE UPDATE OF school_id,user_id,recorded_by ON staff_attendance
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (SELECT school_id FROM users WHERE id=NEW.recorded_by) IS NOT NEW.school_id
                 AND NOT ({_platform_actor_sql('NEW.recorded_by')})
            BEGIN SELECT RAISE(ABORT,'staff attendance school mismatch'); END
        """),
        ("trg_staff_leave_insert_guard", """
            CREATE TRIGGER trg_staff_leave_insert_guard
            BEFORE INSERT ON staff_leave_requests
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (SELECT role FROM users WHERE id=NEW.user_id) IN ('super_admin','student','parent')
            BEGIN SELECT RAISE(ABORT,'staff leave requester school mismatch'); END
        """),
        ("trg_staff_leave_update_guard", f"""
            CREATE TRIGGER trg_staff_leave_update_guard
            BEFORE UPDATE OF school_id,user_id,reviewed_by,status ON staff_leave_requests
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (NEW.reviewed_by IS NOT NULL AND NEW.reviewed_by=NEW.user_id)
              OR (NEW.reviewed_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.reviewed_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.reviewed_by')}))
            BEGIN SELECT RAISE(ABORT,'staff leave reviewer school mismatch'); END
        """),
        ("trg_staff_reset_insert_guard", """
            CREATE TRIGGER trg_staff_reset_insert_guard
            BEFORE INSERT ON staff_password_reset_requests
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (SELECT role FROM users WHERE id=NEW.user_id) IN ('super_admin','student','parent')
            BEGIN SELECT RAISE(ABORT,'staff password reset school mismatch'); END
        """),
        ("trg_staff_reset_update_guard", f"""
            CREATE TRIGGER trg_staff_reset_update_guard
            BEFORE UPDATE OF school_id,user_id,reviewed_by,status ON staff_password_reset_requests
            WHEN (SELECT school_id FROM users WHERE id=NEW.user_id) IS NOT NEW.school_id
              OR (NEW.reviewed_by IS NOT NULL AND
                  (SELECT school_id FROM users WHERE id=NEW.reviewed_by) IS NOT NEW.school_id
                  AND NOT ({_platform_actor_sql('NEW.reviewed_by')}))
            BEGIN SELECT RAISE(ABORT,'staff reset reviewer school mismatch'); END
        """),
        ("trg_school_role_permission_actor", f"""
            CREATE TRIGGER trg_school_role_permission_actor
            BEFORE INSERT ON school_role_permissions
            WHEN NEW.updated_by IS NOT NULL
             AND (SELECT school_id FROM users WHERE id=NEW.updated_by) IS NOT NEW.school_id
             AND NOT ({_platform_actor_sql('NEW.updated_by')})
            BEGIN SELECT RAISE(ABORT,'staff permission actor school mismatch'); END
        """),
    )
    for name, definition in triggers:
        connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        connection.execute(definition)

    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,description) VALUES (6,'staff HR profiles, structure, attendance, leave, recovery and permissions')"
    )


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_date(value):
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    if parsed.isoformat() != value:
        raise ValueError("invalid date")
    return parsed


def _local_today(connection, school_id):
    row = connection.execute("SELECT timezone FROM schools WHERE id=?", (school_id,)).fetchone()
    zone_name = row["timezone"] if row and row["timezone"] else "Africa/Monrovia"
    try:
        return datetime.now(ZoneInfo(zone_name)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()


def _audit(connection, actor_id, school_id, action, target, details=""):
    connection.execute(
        """INSERT INTO audit_log(actor_user_id,action,target_username,details,school_id)
           VALUES (?,?,?,?,?)""",
        (actor_id, action, target, details[:500], school_id),
    )


def _employment_event(connection, school_id, user_id, event_type, actor_id,
                      old_value=None, new_value=None, notes=""):
    connection.execute(
        """INSERT INTO staff_employment_records
           (school_id,user_id,event_type,old_value,new_value,notes,effective_date,recorded_by)
           VALUES (?,?,?,?,?,?,?,?)""",
        (school_id, user_id, event_type, old_value, new_value, notes[:500],
         datetime.now(timezone.utc).date().isoformat(), actor_id),
    )


def register_staff_hr_routes(ns):
    app = ns["app"]
    login_required = ns["login_required"]
    current_user = ns["current_user"]
    request = ns["request"]
    redirect, url_for = ns["redirect"], ns["url_for"]
    render_template, flash, abort = ns["render_template"], ns["flash"], ns["abort"]
    get_db_connection = ns["get_db_connection"]
    require_school_context = ns["require_school_context"]
    generate_password_hash = ns["generate_password_hash"]
    response_class = app.response_class

    def permission(connection, school_id, key, role=None):
        role = role or current_user.role
        if role == "super_admin":
            return bool(current_user.is_super_admin)
        maximum = STAFF_PERMISSION_MAXIMUMS.get(key, set())
        if role not in maximum:
            return False
        row = connection.execute(
            """SELECT enabled FROM school_role_permissions
               WHERE school_id=? AND role=? AND permission_key=?""",
            (school_id, role, key),
        ).fetchone()
        return bool(row["enabled"]) if row else True

    def require_permission(connection, school_id, key):
        if not permission(connection, school_id, key):
            abort(403)

    def ensure_profile(connection, user_id, school_id):
        user = connection.execute(
            "SELECT role,status,approved_by,approved_at FROM users WHERE id=? AND school_id=?",
            (user_id, school_id),
        ).fetchone()
        if user is None or user["role"] in {"student", "parent", "super_admin"}:
            abort(404)
        verification = (
            "pending" if user["status"] == "pending"
            else "verified" if user["approved_by"] is not None
            else "legacy"
        )
        connection.execute(
            """INSERT OR IGNORE INTO staff_profiles
               (school_id,user_id,verification_status,verified_by,verified_at)
               VALUES (?,?,?,?,?)""",
            (school_id, user_id, verification, user["approved_by"], user["approved_at"]),
        )

    def is_staff_user(connection, school_id, user_id):
        return connection.execute(
            """SELECT id,username,full_name,role,status FROM users
               WHERE id=? AND school_id=? AND role NOT IN ('super_admin','student','parent')""",
            (user_id, school_id),
        ).fetchone()

    @app.route("/staff/directory", methods=["GET"])
    @login_required
    def staff_directory():
        school_id = require_school_context()
        connection = get_db_connection()
        try:
            require_permission(connection, school_id, "staff.directory.view")
            query = request.args.get("q", "").strip()[:80]
            role_filter = request.args.get("role", "").strip()
            status_filter = request.args.get("status", "").strip()
            sql = """SELECT u.id,u.username,u.full_name,u.role,u.status,
                            p.employee_number,p.email,p.phone,p.employment_status,
                            d.name AS department_name,pos.name AS position_name,
                            p.verification_status
                     FROM users u LEFT JOIN staff_profiles p
                       ON p.user_id=u.id AND p.school_id=u.school_id
                     LEFT JOIN staff_departments d ON d.id=p.department_id AND d.school_id=u.school_id
                     LEFT JOIN staff_positions pos ON pos.id=p.position_id AND pos.school_id=u.school_id
                     WHERE u.school_id=? AND u.role NOT IN ('super_admin','student','parent')"""
            params = [school_id]
            if query:
                sql += " AND (u.full_name LIKE ? OR u.username LIKE ? OR COALESCE(p.employee_number,'') LIKE ?)"
                params.extend([f"%{query}%"] * 3)
            if role_filter in STAFF_HR_ROLES:
                sql += " AND u.role=?"
                params.append(role_filter)
            if status_filter in {"pending", "active", "suspended"}:
                sql += " AND u.status=?"
                params.append(status_filter)
            sql += " ORDER BY u.full_name COLLATE NOCASE,u.username"
            staff = connection.execute(sql, params).fetchall()
            return render_template(
                "staff_directory.html", staff=staff, query=query,
                role_filter=role_filter, status_filter=status_filter,
            )
        finally:
            connection.close()

    @app.route("/staff/profile", methods=["GET", "POST"])
    @login_required
    def staff_profile():
        school_id = require_school_context()
        if current_user.role not in STAFF_HR_ROLES:
            abort(403)
        connection = get_db_connection()
        try:
            ensure_profile(connection, int(current_user.id), school_id)
            if request.method == "POST":
                require_permission(connection, school_id, "staff.profile.self_edit")
                phone = request.form.get("phone", "").strip()
                email = request.form.get("email", "").strip().lower()
                if len(phone) > 40 or len(email) > 254 or (
                    email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
                ):
                    flash("Check the phone and email values.", "danger")
                    return redirect(url_for("staff_profile"))
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """UPDATE staff_profiles SET phone=?,email=?,updated_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE user_id=? AND school_id=?""",
                    (phone, email, int(current_user.id), int(current_user.id), school_id),
                )
                _audit(connection, int(current_user.id), school_id, "staff_profile_self_updated",
                       current_user.username, "Self-service phone/email update")
                connection.commit()
                flash("Your permitted profile details were updated.", "success")
                return redirect(url_for("staff_profile"))
            profile = connection.execute(
                """SELECT p.*,d.name AS department_name,pos.name AS position_name
                   FROM staff_profiles p
                   LEFT JOIN staff_departments d ON d.id=p.department_id AND d.school_id=p.school_id
                   LEFT JOIN staff_positions pos ON pos.id=p.position_id AND pos.school_id=p.school_id
                   WHERE p.user_id=? AND p.school_id=?""",
                (int(current_user.id), school_id),
            ).fetchone()
            return render_template("staff_profile.html", profile=profile)
        finally:
            connection.close()

    @app.route("/staff/structure", methods=["GET", "POST"])
    @login_required
    def staff_structure():
        school_id = require_school_context()
        connection = get_db_connection()
        try:
            require_permission(connection, school_id, "staff.structure.manage")
            if request.method == "POST":
                action = request.form.get("action", "").strip()
                name = request.form.get("name", "").strip()
                description = request.form.get("description", "").strip()
                if not name or len(name) > 80 or len(description) > 500:
                    flash("Enter a name (80 characters max) and a description (500 characters max).", "danger")
                    return redirect(url_for("staff_structure"))
                connection.execute("BEGIN IMMEDIATE")
                if action == "create_department":
                    connection.execute(
                        """INSERT INTO staff_departments
                           (school_id,name,description,created_by,updated_by)
                           VALUES (?,?,?,?,?)""",
                        (school_id, name, description, int(current_user.id), int(current_user.id)),
                    )
                    audit_action = "staff_department_created"
                elif action == "create_position":
                    rank_text = request.form.get("rank_order", "0").strip()
                    if not rank_text.isdigit() or int(rank_text) > 1000:
                        connection.rollback()
                        flash("Position rank must be between 0 and 1000.", "danger")
                        return redirect(url_for("staff_structure"))
                    connection.execute(
                        """INSERT INTO staff_positions
                           (school_id,name,description,rank_order,created_by,updated_by)
                           VALUES (?,?,?,?,?,?)""",
                        (school_id, name, description, int(rank_text),
                         int(current_user.id), int(current_user.id)),
                    )
                    audit_action = "staff_position_created"
                elif action in {"archive_department", "archive_position"}:
                    record_id = request.form.get("record_id", "").strip()
                    if not record_id.isdigit():
                        connection.rollback()
                        abort(400)
                    table = "staff_departments" if action == "archive_department" else "staff_positions"
                    result = connection.execute(
                        f"UPDATE {table} SET active=0,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?",
                        (int(current_user.id), int(record_id), school_id),
                    )
                    if result.rowcount != 1:
                        connection.rollback()
                        abort(404)
                    audit_action = action + "ed"
                else:
                    connection.rollback()
                    abort(400)
                _audit(connection, int(current_user.id), school_id, audit_action, name, description)
                connection.commit()
                flash("School staff structure updated.", "success")
                return redirect(url_for("staff_structure"))
            departments = connection.execute(
                "SELECT * FROM staff_departments WHERE school_id=? ORDER BY active DESC,name",
                (school_id,),
            ).fetchall()
            positions = connection.execute(
                "SELECT * FROM staff_positions WHERE school_id=? ORDER BY active DESC,rank_order,name",
                (school_id,),
            ).fetchall()
            return render_template("staff_structure.html", departments=departments, positions=positions)
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("That department or position already exists, or is still referenced.", "danger")
            return redirect(url_for("staff_structure"))
        finally:
            connection.close()

    @app.route("/staff/employment/<int:staff_user_id>", methods=["GET", "POST"])
    @login_required
    def staff_employment(staff_user_id):
        school_id = require_school_context()
        connection = get_db_connection()
        try:
            require_permission(connection, school_id, "staff.employment.manage")
            target = is_staff_user(connection, school_id, staff_user_id)
            if target is None:
                abort(404)
            ensure_profile(connection, staff_user_id, school_id)
            if request.method == "POST":
                employee_number = request.form.get("employee_number", "").strip()
                department_text = request.form.get("department_id", "").strip()
                position_text = request.form.get("position_id", "").strip()
                employment_type = request.form.get("employment_type", "full_time").strip()
                employment_status = request.form.get("employment_status", "active").strip()
                hire_date = request.form.get("hire_date", "").strip()
                contract_start = request.form.get("contract_start_date", "").strip()
                contract_end = request.form.get("contract_end_date", "").strip()
                if (
                    len(employee_number) > 40
                    or employment_type not in {"full_time", "part_time", "contract", "volunteer"}
                    or employment_status not in {"active", "on_leave", "terminated"}
                ):
                    flash("Check the employee number and employment selections.", "danger")
                    return redirect(url_for("staff_employment", staff_user_id=staff_user_id))
                for value in (hire_date, contract_start, contract_end):
                    if value:
                        try:
                            _parse_date(value)
                        except ValueError:
                            flash("Employment dates must use YYYY-MM-DD.", "danger")
                            return redirect(url_for("staff_employment", staff_user_id=staff_user_id))
                if contract_start and contract_end and contract_end < contract_start:
                    flash("Contract end date cannot precede the start date.", "danger")
                    return redirect(url_for("staff_employment", staff_user_id=staff_user_id))
                department_id = int(department_text) if department_text.isdigit() else None
                position_id = int(position_text) if position_text.isdigit() else None
                if department_text and department_id is None or position_text and position_id is None:
                    abort(400)
                for table, record_id in (("staff_departments", department_id), ("staff_positions", position_id)):
                    if record_id is not None and connection.execute(
                        f"SELECT 1 FROM {table} WHERE id=? AND school_id=? AND active=1",
                        (record_id, school_id),
                    ).fetchone() is None:
                        abort(404)
                old = connection.execute(
                    "SELECT * FROM staff_profiles WHERE user_id=? AND school_id=?",
                    (staff_user_id, school_id),
                ).fetchone()
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """UPDATE staff_profiles
                       SET employee_number=?,department_id=?,position_id=?,employment_type=?,
                           employment_status=?,hire_date=?,contract_start_date=?,contract_end_date=?,
                           updated_by=?,updated_at=CURRENT_TIMESTAMP
                       WHERE user_id=? AND school_id=?""",
                    (employee_number or None, department_id, position_id, employment_type,
                     employment_status, hire_date or None, contract_start or None, contract_end or None,
                     int(current_user.id), staff_user_id, school_id),
                )
                if old["department_id"] != department_id:
                    _employment_event(connection, school_id, staff_user_id, "department_changed",
                                      int(current_user.id), str(old["department_id"]), str(department_id))
                if old["position_id"] != position_id:
                    _employment_event(connection, school_id, staff_user_id, "position_changed",
                                      int(current_user.id), str(old["position_id"]), str(position_id))
                if old["employment_status"] != employment_status:
                    _employment_event(connection, school_id, staff_user_id, "employment_status_changed",
                                      int(current_user.id), old["employment_status"], employment_status)
                if old["employment_type"] != employment_type:
                    _employment_event(connection, school_id, staff_user_id, "employment_type_changed",
                                      int(current_user.id), old["employment_type"], employment_type)
                _employment_event(connection, school_id, staff_user_id, "profile_updated",
                                  int(current_user.id), None, employee_number or "no employee number")
                _audit(connection, int(current_user.id), school_id, "staff_employment_updated",
                       target["username"], "Employment profile updated")
                connection.commit()
                flash("Employment record updated.", "success")
                return redirect(url_for("staff_employment", staff_user_id=staff_user_id))
            profile = connection.execute(
                """SELECT p.*,d.name AS department_name,pos.name AS position_name
                   FROM staff_profiles p
                   LEFT JOIN staff_departments d ON d.id=p.department_id AND d.school_id=p.school_id
                   LEFT JOIN staff_positions pos ON pos.id=p.position_id AND pos.school_id=p.school_id
                   WHERE p.user_id=? AND p.school_id=?""",
                (staff_user_id, school_id),
            ).fetchone()
            departments = connection.execute(
                "SELECT id,name FROM staff_departments WHERE school_id=? AND active=1 ORDER BY name",
                (school_id,),
            ).fetchall()
            positions = connection.execute(
                "SELECT id,name FROM staff_positions WHERE school_id=? AND active=1 ORDER BY rank_order,name",
                (school_id,),
            ).fetchall()
            history = connection.execute(
                """SELECT e.*,u.username AS recorded_by_name FROM staff_employment_records e
                   LEFT JOIN users u ON u.id=e.recorded_by
                   WHERE e.school_id=? AND e.user_id=? ORDER BY e.created_at DESC LIMIT 50""",
                (school_id, staff_user_id),
            ).fetchall()
            return render_template(
                "staff_employment.html", target=target, profile=profile,
                departments=departments, positions=positions, history=history,
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("Employee number is already used in this school or the record violates a school constraint.", "danger")
            return redirect(url_for("staff_employment", staff_user_id=staff_user_id))
        finally:
            connection.close()

    @app.route("/staff/attendance", methods=["GET", "POST"])
    @login_required
    def staff_attendance():
        school_id = require_school_context()
        if current_user.role not in STAFF_HR_ROLES:
            abort(403)
        connection = get_db_connection()
        try:
            manage = permission(connection, school_id, "staff.attendance.manage")
            view_all = permission(connection, school_id, "staff.attendance.view_all")
            self_allowed = permission(connection, school_id, "staff.attendance.self")
            if request.method == "POST":
                action = request.form.get("action", "").strip()
                today = _local_today(connection, school_id).isoformat()
                if action in {"clock_in", "clock_out"}:
                    if not self_allowed:
                        abort(403)
                    target_id = int(current_user.id)
                    existing = connection.execute(
                        """SELECT * FROM staff_attendance
                           WHERE school_id=? AND user_id=? AND attendance_date=?""",
                        (school_id, target_id, today),
                    ).fetchone()
                    connection.execute("BEGIN IMMEDIATE")
                    now = _utc_now()
                    if action == "clock_in":
                        if existing:
                            connection.rollback()
                            flash("You already have an attendance record for today.", "warning")
                            return redirect(url_for("staff_attendance"))
                        connection.execute(
                            """INSERT INTO staff_attendance
                               (school_id,user_id,attendance_date,status,check_in_at,recorded_by)
                               VALUES (?,?,?,'present',?,?)""",
                            (school_id, target_id, today, now, target_id),
                        )
                    else:
                        if existing is None or not existing["check_in_at"] or existing["check_out_at"]:
                            connection.rollback()
                            flash("Clock in first; an existing clock-out cannot be repeated.", "warning")
                            return redirect(url_for("staff_attendance"))
                        connection.execute(
                            """UPDATE staff_attendance SET check_out_at=?,updated_at=CURRENT_TIMESTAMP
                               WHERE id=? AND school_id=? AND user_id=?""",
                            (now, existing["id"], school_id, target_id),
                        )
                    _audit(connection, target_id, school_id, "staff_attendance_" + action,
                           current_user.username, today)
                    connection.commit()
                    flash("Your attendance was recorded.", "success")
                    return redirect(url_for("staff_attendance"))
                if action == "mark":
                    if not manage:
                        abort(403)
                    target_text = request.form.get("user_id", "").strip()
                    status = request.form.get("status", "").strip()
                    date_text = request.form.get("attendance_date", today).strip()
                    note = request.form.get("note", "").strip()
                    if not target_text.isdigit() or status not in {"present", "absent", "late", "excused", "remote"} or len(note) > 500:
                        abort(400)
                    try:
                        _parse_date(date_text)
                    except ValueError:
                        abort(400)
                    target = is_staff_user(connection, school_id, int(target_text))
                    if target is None:
                        abort(404)
                    connection.execute("BEGIN IMMEDIATE")
                    old = connection.execute(
                        """SELECT * FROM staff_attendance WHERE school_id=? AND user_id=? AND attendance_date=?""",
                        (school_id, target["id"], date_text),
                    ).fetchone()
                    connection.execute(
                        """INSERT INTO staff_attendance
                           (school_id,user_id,attendance_date,status,note,recorded_by)
                           VALUES (?,?,?,?,?,?)
                           ON CONFLICT(school_id,user_id,attendance_date) DO UPDATE SET
                             status=excluded.status,note=excluded.note,recorded_by=excluded.recorded_by,
                             updated_at=CURRENT_TIMESTAMP""",
                        (school_id, target["id"], date_text, status, note, int(current_user.id)),
                    )
                    _audit(connection, int(current_user.id), school_id, "staff_attendance_marked",
                           target["username"], f"{date_text}:{status}")
                    connection.commit()
                    flash("Staff attendance saved.", "success")
                    return redirect(url_for("staff_attendance"))
                abort(400)

            today = _local_today(connection, school_id).isoformat()
            if view_all:
                rows = connection.execute(
                    """SELECT a.*,u.username,u.full_name,p.employee_number,d.name AS department_name
                       FROM staff_attendance a JOIN users u ON u.id=a.user_id AND u.school_id=a.school_id
                       LEFT JOIN staff_profiles p ON p.user_id=u.id AND p.school_id=u.school_id
                       LEFT JOIN staff_departments d ON d.id=p.department_id AND d.school_id=u.school_id
                       WHERE a.school_id=? ORDER BY a.attendance_date DESC,u.full_name LIMIT 300""",
                    (school_id,),
                ).fetchall()
                staff = connection.execute(
                    """SELECT id,username,full_name FROM users
                       WHERE school_id=? AND role NOT IN ('super_admin','student','parent')
                         AND status='active' ORDER BY full_name""",
                    (school_id,),
                ).fetchall() if manage else []
            else:
                rows = connection.execute(
                    "SELECT * FROM staff_attendance WHERE school_id=? AND user_id=? ORDER BY attendance_date DESC LIMIT 90",
                    (school_id, int(current_user.id)),
                ).fetchall()
                staff = []
            today_record = connection.execute(
                "SELECT * FROM staff_attendance WHERE school_id=? AND user_id=? AND attendance_date=?",
                (school_id, int(current_user.id), today),
            ).fetchone()
            return render_template(
                "staff_attendance.html", rows=rows, staff=staff, today=today,
                today_record=today_record, can_manage=manage, can_view_all=view_all,
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("An attendance record already exists or violates a school constraint.", "danger")
            return redirect(url_for("staff_attendance"))
        finally:
            connection.close()

    @app.route("/staff/leave", methods=["GET", "POST"])
    @login_required
    def staff_leave():
        school_id = require_school_context()
        if current_user.role not in STAFF_HR_ROLES:
            abort(403)
        connection = get_db_connection()
        try:
            can_approve = permission(connection, school_id, "staff.leave.approve")
            can_request = permission(connection, school_id, "staff.leave.request")
            if request.method == "POST":
                action = request.form.get("action", "").strip()
                if action == "request":
                    if not can_request:
                        abort(403)
                    leave_type = request.form.get("leave_type", "").strip()
                    start_text = request.form.get("start_date", "").strip()
                    end_text = request.form.get("end_date", "").strip()
                    reason = request.form.get("reason", "").strip()
                    if leave_type not in {"annual","sick","maternity","paternity","bereavement","unpaid","other"} or not reason or len(reason) > 1000:
                        flash("Complete the leave type and reason.", "danger")
                        return redirect(url_for("staff_leave"))
                    try:
                        start_date = _parse_date(start_text)
                        end_date = _parse_date(end_text)
                        if end_date < start_date:
                            raise ValueError("end before start")
                    except ValueError:
                        flash("Enter valid leave dates; the end date must not precede the start date.", "danger")
                        return redirect(url_for("staff_leave"))
                    overlap = connection.execute(
                        """SELECT 1 FROM staff_leave_requests
                           WHERE school_id=? AND user_id=? AND status IN ('pending','approved')
                             AND start_date<=? AND end_date>=? LIMIT 1""",
                        (school_id, int(current_user.id), end_text, start_text),
                    ).fetchone()
                    if overlap:
                        flash("You already have a pending or approved leave request overlapping those dates.", "danger")
                        return redirect(url_for("staff_leave"))
                    connection.execute("BEGIN IMMEDIATE")
                    cursor = connection.execute(
                        """INSERT INTO staff_leave_requests
                           (school_id,user_id,leave_type,start_date,end_date,reason)
                           VALUES (?,?,?,?,?,?)""",
                        (school_id, int(current_user.id), leave_type, start_text, end_text, reason),
                    )
                    _audit(connection, int(current_user.id), school_id, "staff_leave_requested",
                           current_user.username, f"{leave_type}:{start_text}:{end_text}")
                    connection.commit()
                    flash("Your leave request was submitted for review.", "success")
                    return redirect(url_for("staff_leave"))
                if action in {"approve","reject"}:
                    if not can_approve:
                        abort(403)
                    request_text = request.form.get("request_id", "").strip()
                    note = request.form.get("reviewer_note", "").strip()
                    if not request_text.isdigit() or len(note) > 1000:
                        abort(400)
                    leave = connection.execute(
                        "SELECT * FROM staff_leave_requests WHERE id=? AND school_id=?",
                        (int(request_text), school_id),
                    ).fetchone()
                    if leave is None:
                        abort(404)
                    if int(leave["user_id"]) == int(current_user.id):
                        abort(403)
                    if leave["status"] != "pending":
                        flash("Only pending leave requests can be reviewed.", "warning")
                        return redirect(url_for("staff_leave"))
                    status = "approved" if action == "approve" else "rejected"
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """UPDATE staff_leave_requests SET status=?,reviewed_by=?,reviewed_at=?,
                           reviewer_note=?,updated_at=CURRENT_TIMESTAMP
                           WHERE id=? AND school_id=? AND status='pending'""",
                        (status, int(current_user.id), _utc_now(), note,
                         int(request_text), school_id),
                    )
                    _audit(connection, int(current_user.id), school_id, "staff_leave_" + status,
                           str(leave["user_id"]), f"{leave['leave_type']}:{leave['start_date']}:{leave['end_date']}")
                    connection.commit()
                    flash("Leave request " + status + ".", "success")
                    return redirect(url_for("staff_leave"))
                if action == "cancel":
                    leave_text = request.form.get("request_id", "").strip()
                    if not leave_text.isdigit():
                        abort(400)
                    leave = connection.execute(
                        "SELECT * FROM staff_leave_requests WHERE id=? AND school_id=? AND user_id=?",
                        (int(leave_text), school_id, int(current_user.id)),
                    ).fetchone()
                    if leave is None:
                        abort(404)
                    if leave["status"] != "pending":
                        abort(403)
                    connection.execute(
                        """UPDATE staff_leave_requests SET status='cancelled',updated_at=CURRENT_TIMESTAMP
                           WHERE id=? AND school_id=? AND user_id=? AND status='pending'""",
                        (int(leave_text), school_id, int(current_user.id)),
                    )
                    _audit(connection, int(current_user.id), school_id, "staff_leave_cancelled",
                           current_user.username, str(leave_text))
                    connection.commit()
                    flash("Pending leave request cancelled.", "success")
                    return redirect(url_for("staff_leave"))
                abort(400)

            if can_approve:
                rows = connection.execute(
                    """SELECT l.*,u.username,u.full_name,reviewer.username AS reviewer_username
                       FROM staff_leave_requests l
                       JOIN users u ON u.id=l.user_id AND u.school_id=l.school_id
                       LEFT JOIN users reviewer ON reviewer.id=l.reviewed_by
                       WHERE l.school_id=? ORDER BY CASE l.status WHEN 'pending' THEN 0 ELSE 1 END,
                             l.requested_at DESC LIMIT 300""",
                    (school_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM staff_leave_requests
                       WHERE school_id=? AND user_id=? ORDER BY requested_at DESC LIMIT 100""",
                    (school_id, int(current_user.id)),
                ).fetchall()
            return render_template("staff_leave.html", rows=rows, can_approve=can_approve)
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("The leave request conflicts with an existing school record.", "danger")
            return redirect(url_for("staff_leave"))
        finally:
            connection.close()

    @app.route("/staff/<int:staff_user_id>/activity", methods=["GET"])
    @login_required
    def staff_activity(staff_user_id):
        school_id = require_school_context()
        connection = get_db_connection()
        try:
            require_permission(connection, school_id, "staff.activity.view")
            target = is_staff_user(connection, school_id, staff_user_id)
            if target is None:
                abort(404)
            events = connection.execute(
                """SELECT a.*,actor.username AS actor_username
                   FROM audit_log a LEFT JOIN users actor ON actor.id=a.actor_user_id
                   WHERE a.school_id=? AND (a.actor_user_id=? OR a.target_username=?)
                   ORDER BY a.created_at DESC,a.id DESC LIMIT 100""",
                (school_id, staff_user_id, target["username"]),
            ).fetchall()
            return render_template("staff_activity.html", target=target, events=events)
        finally:
            connection.close()

    @app.route("/staff/permissions", methods=["GET", "POST"])
    @login_required
    def staff_permissions():
        school_id = require_school_context()
        connection = get_db_connection()
        try:
            require_permission(connection, school_id, "staff.permissions.manage")
            if request.method == "POST":
                role = request.form.get("role", "").strip()
                key = request.form.get("permission_key", "").strip()
                enabled_text = request.form.get("enabled", "").strip()
                if (
                    role not in STAFF_HR_ROLES
                    or key not in STAFF_PERMISSION_MAXIMUMS
                    or role not in STAFF_PERMISSION_MAXIMUMS[key]
                    or enabled_text not in {"0","1"}
                    or (current_user.role == "school_admin" and role == "school_admin")
                ):
                    abort(403)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO school_role_permissions
                       (school_id,role,permission_key,enabled,updated_by)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(school_id,role,permission_key) DO UPDATE SET
                         enabled=excluded.enabled,updated_by=excluded.updated_by,
                         updated_at=CURRENT_TIMESTAMP""",
                    (school_id, role, key, int(enabled_text), int(current_user.id)),
                )
                _audit(connection, int(current_user.id), school_id, "staff_permission_updated",
                       role, f"{key}={'enabled' if enabled_text == '1' else 'disabled'}")
                connection.commit()
                flash("Staff permission override saved within the platform role ceiling.", "success")
                return redirect(url_for("staff_permissions"))
            rows = []
            for role in sorted(STAFF_HR_ROLES):
                if role == "school_admin" and current_user.role == "school_admin":
                    continue
                for key, maximum in STAFF_PERMISSION_MAXIMUMS.items():
                    if role not in maximum:
                        continue
                    override = connection.execute(
                        """SELECT enabled FROM school_role_permissions
                           WHERE school_id=? AND role=? AND permission_key=?""",
                        (school_id, role, key),
                    ).fetchone()
                    rows.append({
                        "role": role,
                        "permission_key": key,
                        "label": STAFF_PERMISSION_LABELS[key],
                        "enabled": bool(override["enabled"]) if override else True,
                    })
            return render_template("staff_permissions.html", rows=rows)
        finally:
            connection.close()

    @app.route("/staff/password-recovery/request", methods=["GET", "POST"])
    def staff_password_recovery_request():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            school_slug = request.form.get("school_slug", "").strip()
            ip_value = request.remote_addr or "unknown"
            ip_hash = hashlib.sha256(ip_value.encode("utf-8")).hexdigest()
            connection = get_db_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = datetime.now(timezone.utc).replace(microsecond=0)
                rate = connection.execute(
                    "SELECT * FROM staff_password_reset_rate_limits WHERE ip_hash=?",
                    (ip_hash,),
                ).fetchone()
                if rate is None:
                    connection.execute(
                        """INSERT INTO staff_password_reset_rate_limits
                           (ip_hash,attempts,window_started_at) VALUES (?,1,?)""",
                        (ip_hash, now.isoformat()),
                    )
                    allowed = True
                else:
                    blocked = bool(rate["blocked_until"] and rate["blocked_until"] > now.isoformat())
                    window_start = datetime.fromisoformat(rate["window_started_at"])
                    if (now - window_start).total_seconds() > 900:
                        connection.execute(
                            """UPDATE staff_password_reset_rate_limits
                               SET attempts=1,window_started_at=?,blocked_until=NULL,updated_at=CURRENT_TIMESTAMP
                               WHERE ip_hash=?""",
                            (now.isoformat(), ip_hash),
                        )
                        allowed = True
                    elif blocked or rate["attempts"] >= 5:
                        connection.execute(
                            """UPDATE staff_password_reset_rate_limits
                               SET blocked_until=?,updated_at=CURRENT_TIMESTAMP WHERE ip_hash=?""",
                            ((now + timedelta(minutes=15)).isoformat(), ip_hash),
                        )
                        allowed = False
                    else:
                        connection.execute(
                            """UPDATE staff_password_reset_rate_limits
                               SET attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE ip_hash=?""",
                            (ip_hash,),
                        )
                        allowed = True
                if allowed and username and school_slug:
                    user = connection.execute(
                        """SELECT u.id,u.school_id FROM users u JOIN schools s ON s.id=u.school_id
                           WHERE u.username=? AND s.slug=? AND s.status='active'
                             AND u.status='active'
                             AND u.role NOT IN ('super_admin','student','parent')""",
                        (username, school_slug),
                    ).fetchone()
                    if user:
                        connection.execute(
                            """INSERT INTO staff_password_reset_requests(school_id,user_id)
                               VALUES (?,?)""",
                            (user["school_id"], user["id"]),
                        )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
            finally:
                connection.close()
            flash("If the account is eligible, a recovery request has been submitted for school review.", "success")
            return redirect(url_for("login"))
        connection = get_db_connection()
        try:
            schools = connection.execute(
                "SELECT name,slug FROM schools WHERE status='active' ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
        return render_template("staff_password_recovery_request.html", schools=schools)

    @app.route("/staff/password-recovery/manage", methods=["GET", "POST"])
    @login_required
    def staff_password_recovery_manage():
        school_id = require_school_context()
        connection = get_db_connection()
        try:
            require_permission(connection, school_id, "staff.password.reset")
            issued_token = None
            issued_username = None
            if request.method == "POST":
                action = request.form.get("action", "").strip()
                request_text = request.form.get("request_id", "").strip()
                if not request_text.isdigit():
                    abort(400)
                item = connection.execute(
                    """SELECT r.*,u.username FROM staff_password_reset_requests r
                       JOIN users u ON u.id=r.user_id AND u.school_id=r.school_id
                       WHERE r.id=? AND r.school_id=? AND r.status='pending'""",
                    (int(request_text), school_id),
                ).fetchone()
                if item is None:
                    abort(404)
                connection.execute("BEGIN IMMEDIATE")
                if action == "issue":
                    issued_token = secrets.token_urlsafe(32)
                    token_hash = hashlib.sha256(issued_token.encode("utf-8")).hexdigest()
                    expiry = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat()
                    connection.execute(
                        """UPDATE staff_password_reset_requests
                           SET status='issued',token_hash=?,token_expires_at=?,
                               reviewed_by=?,reviewed_at=? WHERE id=? AND school_id=? AND status='pending'""",
                        (token_hash, expiry, int(current_user.id), _utc_now(), int(request_text), school_id),
                    )
                    issued_username = item["username"]
                    _audit(connection, int(current_user.id), school_id, "staff_password_reset_token_issued",
                           item["username"], "One-time reset code issued; token not recorded")
                elif action == "reject":
                    connection.execute(
                        """UPDATE staff_password_reset_requests
                           SET status='rejected',reviewed_by=?,reviewed_at=?
                           WHERE id=? AND school_id=? AND status='pending'""",
                        (int(current_user.id), _utc_now(), int(request_text), school_id),
                    )
                    _audit(connection, int(current_user.id), school_id, "staff_password_reset_rejected",
                           item["username"], "Recovery request rejected")
                else:
                    connection.rollback()
                    abort(400)
                connection.commit()
                if issued_token:
                    pending = connection.execute(
                        """SELECT r.id,r.status,r.requested_at,u.username,u.full_name
                           FROM staff_password_reset_requests r JOIN users u ON u.id=r.user_id
                           WHERE r.school_id=? AND r.status='pending' ORDER BY r.requested_at""",
                        (school_id,),
                    ).fetchall()
                    return render_template(
                        "staff_password_recovery_manage.html", pending=pending,
                        issued_token=issued_token, issued_username=issued_username,
                    )
                flash("Recovery request updated.", "success")
                return redirect(url_for("staff_password_recovery_manage"))
            pending = connection.execute(
                """SELECT r.id,r.status,r.requested_at,u.username,u.full_name
                   FROM staff_password_reset_requests r JOIN users u ON u.id=r.user_id
                   WHERE r.school_id=? AND r.status='pending' ORDER BY r.requested_at""",
                (school_id,),
            ).fetchall()
            return render_template(
                "staff_password_recovery_manage.html", pending=pending,
                issued_token=None, issued_username=None,
            )
        finally:
            connection.close()

    @app.route("/staff/password-recovery/complete", methods=["GET", "POST"])
    def staff_password_recovery_complete():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            school_slug = request.form.get("school_slug", "").strip()
            token = request.form.get("recovery_code", "").strip()
            password = request.form.get("new_password", "")
            if len(password) < 12 or len(password) > 128 or not token:
                flash("Enter a valid recovery code and a password of at least 12 characters.", "danger")
                return redirect(url_for("staff_password_recovery_complete"))
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            connection = get_db_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                item = connection.execute(
                    """SELECT r.id,r.school_id,r.user_id,u.username
                       FROM staff_password_reset_requests r
                       JOIN users u ON u.id=r.user_id AND u.school_id=r.school_id
                       JOIN schools s ON s.id=r.school_id
                       WHERE u.username=? AND s.slug=? AND s.status='active'
                         AND u.status='active' AND r.status='issued'
                         AND r.token_hash=? AND r.token_used_at IS NULL
                         AND r.token_expires_at > ?""",
                    (username, school_slug, token_hash, _utc_now()),
                ).fetchone()
                if item is None:
                    connection.rollback()
                    flash("The recovery details are invalid or expired. Request a new code from your school.", "danger")
                    return redirect(url_for("staff_password_recovery_complete"))
                connection.execute(
                    "UPDATE users SET password_hash=? WHERE id=? AND school_id=?",
                    (generate_password_hash(password), item["user_id"], item["school_id"]),
                )
                connection.execute(
                    """UPDATE staff_password_reset_requests
                       SET status='completed',token_used_at=?,reviewed_at=?
                       WHERE id=? AND school_id=? AND status='issued' AND token_used_at IS NULL""",
                    (_utc_now(), _utc_now(), item["id"], item["school_id"]),
                )
                _audit(connection, item["user_id"], item["school_id"],
                       "staff_password_reset_completed", username,
                       "Password reset completed using a single-use recovery code")
                connection.commit()
                flash("Password updated. You can now sign in.", "success")
                return redirect(url_for("login"))
            finally:
                connection.close()
        connection = get_db_connection()
        try:
            schools = connection.execute(
                "SELECT name,slug FROM schools WHERE status='active' ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
        return render_template("staff_password_recovery_complete.html", schools=schools)

    # Expose the same policy function to the existing Staff Management module.
    ns["has_staff_permission"] = lambda school_id, key, role=None: _permission_with_connection(
        get_db_connection, current_user, school_id, key, role
    )


def _permission_with_connection(get_db_connection, current_user, school_id, key, role=None):
    role = role or current_user.role
    if role == "super_admin":
        return bool(current_user.is_super_admin)
    maximum = STAFF_PERMISSION_MAXIMUMS.get(key, set())
    if role not in maximum:
        return False
    connection = get_db_connection()
    try:
        row = connection.execute(
            """SELECT enabled FROM school_role_permissions
               WHERE school_id=? AND role=? AND permission_key=?""",
            (school_id, role, key),
        ).fetchone()
        return bool(row["enabled"]) if row else True
    finally:
        connection.close()
