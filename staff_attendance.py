"""School-scoped staff administration and attendance reporting routes."""
import csv
import io
from datetime import datetime, timedelta


def register_staff_attendance_routes(ns):
    app = ns["app"]
    login_required = ns["login_required"]
    current_user = ns["current_user"]
    request = ns["request"]
    redirect, url_for = ns["redirect"], ns["url_for"]
    render_template, flash, abort = ns["render_template"], ns["flash"], ns["abort"]
    get_db_connection = ns["get_db_connection"]
    require_school_context = ns["require_school_context"]
    generate_password_hash = ns["generate_password_hash"]
    staff_roles = ns["STAFF_ROLES"]
    get_school_class_levels = ns["get_school_class_levels"]
    get_school_subjects = ns["get_school_subjects"]

    staff_manage_roles = {"super_admin", "school_admin", "principal", "vice_principal"}
    attendance_report_roles = {
        "super_admin", "school_admin", "principal", "vice_principal",
        "registrar", "academic_admin", "attendance_officer", "teacher",
    }
    school_admin_assignable = {
        "principal", "vice_principal", "registrar", "admissions_officer",
        "academic_admin", "attendance_officer", "teacher", "finance_admin",
        "librarian", "counselor",
    }
    leadership_assignable = {"teacher", "attendance_officer", "librarian", "counselor"}
    leadership_manageable = leadership_assignable | {"registrar"}
    all_school_assignable = school_admin_assignable | {"school_admin"}

    def audit_for_school(connection, action, target, details, school_id):
        # Explicit school ID is important when a platform Super Admin acts inside
        # a selected school, because their user row intentionally has school_id NULL.
        connection.execute(
            """INSERT INTO audit_log
               (actor_user_id, action, target_username, details, school_id)
               VALUES (?, ?, ?, ?, ?)""",
            (int(current_user.id), action, target, details, school_id),
        )

    @app.route("/staff", methods=["GET", "POST"])
    @login_required
    def staff_management():
        if current_user.role not in staff_manage_roles:
            abort(403)
        school_id = require_school_context()
        assignable = (
            all_school_assignable if current_user.role == "super_admin"
            else school_admin_assignable if current_user.role == "school_admin"
            else leadership_assignable
        )
        manageable = (
            all_school_assignable if current_user.role == "super_admin"
            else school_admin_assignable if current_user.role == "school_admin"
            else leadership_manageable
        )

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            connection = get_db_connection()
            try:
                class_levels = get_school_class_levels(connection, school_id)
                subject_names = {row["name"] for row in get_school_subjects(connection, school_id)}
                if action == "assign_teacher":
                    teacher_text = request.form.get("teacher_id", "").strip()
                    class_level = request.form.get("class_level", "").strip()
                    subject = request.form.get("subject", "").strip()
                    if class_level and class_level not in class_levels and len(class_level)<=80:
                        connection.execute(
                            "INSERT OR IGNORE INTO grade_levels(school_id,name,sequence_no) VALUES(?,?,COALESCE((SELECT MAX(sequence_no)+1 FROM grade_levels WHERE school_id=?),1))",
                            (school_id,class_level,school_id),
                        )
                        class_levels.append(class_level)
                    if subject and subject not in subject_names and len(subject)<=80:
                        connection.execute(
                            "INSERT OR IGNORE INTO subjects(school_id,name) VALUES(?,?)",
                            (school_id,subject),
                        )
                        subject_names.add(subject)
                    if (not teacher_text.isdigit() or class_level not in class_levels
                            or subject not in subject_names or len(subject) > 80
                            or any(ord(ch) < 32 or ord(ch) == 127 for ch in subject)):
                        abort(400)
                    teacher = connection.execute(
                        """SELECT id, username FROM users
                           WHERE id = ? AND school_id = ? AND role = 'teacher'
                             AND status = 'active' AND is_super_admin = 0""",
                        (int(teacher_text), school_id),
                    ).fetchone()
                    if teacher is None:
                        abort(404)
                    connection.execute(
                        """INSERT INTO teacher_class_assignments
                           (school_id, teacher_id, class_level, subject, active, created_by)
                           VALUES (?, ?, ?, ?, 1, ?)
                           ON CONFLICT(school_id, teacher_id, class_level, subject)
                           DO UPDATE SET active = 1, created_by = excluded.created_by,
                                         updated_at = CURRENT_TIMESTAMP""",
                        (school_id, teacher["id"], class_level, subject, int(current_user.id)),
                    )
                    audit_for_school(connection, "teacher_assignment_activated",
                                     teacher["username"],
                                     "class=" + class_level + "; subject=" + subject, school_id)
                    connection.commit()
                    flash("Teacher class and subject assignment saved.", "success")
                    return redirect(url_for("staff_management"))

                if action == "revoke_teacher_assignment":
                    assignment_text = request.form.get("assignment_id", "").strip()
                    if not assignment_text.isdigit():
                        abort(400)
                    assignment = connection.execute(
                        """SELECT a.id, u.username FROM teacher_class_assignments a
                           JOIN users u ON u.id = a.teacher_id AND u.school_id = a.school_id
                           WHERE a.id = ? AND a.school_id = ? AND a.active = 1""",
                        (int(assignment_text), school_id),
                    ).fetchone()
                    if assignment is None:
                        abort(404)
                    connection.execute(
                        """UPDATE teacher_class_assignments
                           SET active = 0, updated_at = CURRENT_TIMESTAMP
                           WHERE id = ? AND school_id = ? AND active = 1""",
                        (assignment["id"], school_id),
                    )
                    audit_for_school(connection, "teacher_assignment_revoked",
                                     assignment["username"], "assignment_id=" + str(assignment["id"]),
                                     school_id)
                    connection.commit()
                    flash("Teacher assignment revoked. Historical audit records are preserved.", "success")
                    return redirect(url_for("staff_management"))

                if action == "create":
                    username = request.form.get("username", "").strip()
                    full_name = request.form.get("full_name", "").strip()
                    role = request.form.get("role", "").strip()
                    password = request.form.get("temporary_password", "")
                    if (not username or len(username) > 64
                        or not username.replace("_", "").replace("-", "").isalnum()
                        or not full_name or len(full_name) > 120
                        or role not in assignable or role not in staff_roles
                        or len(password) < 12 or len(password) > 256):
                        flash("Enter a valid name, username, permitted role, and temporary password of 12–256 characters.", "danger")
                        return redirect(url_for("staff_management"))
                    try:
                        new_user = connection.execute(
                            """INSERT INTO users
                               (username, full_name, password_hash, role, status, is_super_admin,
                                school_id, approved_by, approved_at)
                               VALUES (?, ?, ?, ?, 'active', 0, ?, ?, CURRENT_TIMESTAMP)""",
                            (username, full_name, generate_password_hash(password), role,
                             school_id, int(current_user.id)),
                        )
                        new_user_id = new_user.lastrowid
                        connection.execute(
                            """INSERT INTO staff_memberships(school_id,user_id,membership_status)
                               VALUES(?,?,'active')""",
                            (school_id,new_user_id),
                        )
                        membership_id = connection.execute(
                            "SELECT id FROM staff_memberships WHERE school_id=? AND user_id=?",
                            (school_id,new_user_id),
                        ).fetchone()["id"]
                        connection.execute(
                            "INSERT INTO staff_profiles(school_id,membership_id) VALUES(?,?)",
                            (school_id,membership_id),
                        )
                        connection.execute(
                            """INSERT INTO staff_employment(school_id,membership_id,job_title,employment_status)
                               VALUES(?,?,?,'active')""",
                            (school_id,membership_id,staff_roles[role]),
                        )
                        connection.execute(
                            """INSERT INTO staff_lifecycle_events
                               (school_id,membership_id,event_type,event_date,actor_user_id)
                               VALUES(?,?,'onboarded',date('now'),?)""",
                            (school_id,membership_id,int(current_user.id)),
                        )
                        audit_for_school(connection, "staff_account_created", username,
                                         "Role: " + role, school_id)
                        connection.commit()
                        flash("Staff account created for this school. Share the temporary password privately.", "success")
                    except Exception as error:
                        connection.rollback()
                        # Avoid returning raw database errors or account existence details.
                        if error.__class__.__name__ == "IntegrityError":
                            flash("That username is already in use. Choose a different username.", "danger")
                        else:
                            raise
                    return redirect(url_for("staff_management"))

                target_text = request.form.get("user_id", "").strip()
                if not target_text.isdigit():
                    abort(400)
                target = connection.execute(
                    """SELECT id, username, role, status, is_super_admin
                       FROM users WHERE id = ? AND school_id = ?""",
                    (int(target_text), school_id),
                ).fetchone()
                if target is None:
                    abort(404)
                if target["is_super_admin"] or target["role"] == "super_admin":
                    abort(403)
                if int(target["id"]) == int(current_user.id):
                    flash("You cannot suspend or change the role of your own account here.", "danger")
                    return redirect(url_for("staff_management"))
                if target["role"] not in manageable:
                    abort(403)

                if action in {"suspend", "reactivate"}:
                    if action == "suspend" and target["status"] != "active":
                        flash("Only active accounts can be suspended.", "info")
                    elif action == "reactivate" and target["status"] != "suspended":
                        flash("Only suspended accounts can be reactivated.", "info")
                    else:
                        status = "suspended" if action == "suspend" else "active"
                        connection.execute(
                            "UPDATE users SET status = ? WHERE id = ? AND school_id = ? AND is_super_admin = 0",
                            (status, target["id"], school_id),
                        )
                        membership_status = "suspended" if action == "suspend" else "active"
                        employment_status = "suspended" if action == "suspend" else "active"
                        connection.execute(
                            """UPDATE staff_memberships SET membership_status=?,updated_at=CURRENT_TIMESTAMP
                               WHERE school_id=? AND user_id=?""",
                            (membership_status,school_id,target["id"]),
                        )
                        connection.execute(
                            """UPDATE staff_employment SET employment_status=?,updated_at=CURRENT_TIMESTAMP
                               WHERE school_id=? AND membership_id IN
                                 (SELECT id FROM staff_memberships WHERE school_id=? AND user_id=?)""",
                            (employment_status,school_id,school_id,target["id"]),
                        )
                        if action == "suspend" and target["role"] == "teacher":
                            revoked_count = connection.execute(
                                """UPDATE teacher_class_assignments
                                   SET active = 0, updated_at = CURRENT_TIMESTAMP
                                   WHERE school_id = ? AND teacher_id = ? AND active = 1""",
                                (school_id, target["id"]),
                            ).rowcount
                            if revoked_count:
                                audit_for_school(
                                    connection, "teacher_assignments_revoked_account_suspended",
                                    target["username"], "revoked_assignments=" + str(revoked_count), school_id,
                                )
                        audit_for_school(connection, "staff_account_" + action, target["username"],
                                         "Status changed to " + status, school_id)
                        connection.commit()
                        flash("Staff account status updated.", "success")
                elif action == "change_role":
                    role = request.form.get("role", "").strip()
                    if role not in assignable or role not in staff_roles:
                        abort(403)
                    if target["role"] in {"student", "parent"}:
                        abort(403)
                    connection.execute(
                        "UPDATE users SET role = ? WHERE id = ? AND school_id = ? AND is_super_admin = 0",
                        (role, target["id"], school_id),
                    )
                    if target["role"] == "teacher" and role != "teacher":
                        revoked_count = connection.execute(
                            """UPDATE teacher_class_assignments
                               SET active = 0, updated_at = CURRENT_TIMESTAMP
                               WHERE school_id = ? AND teacher_id = ? AND active = 1""",
                            (school_id, target["id"]),
                        ).rowcount
                        if revoked_count:
                            audit_for_school(
                                connection, "teacher_assignments_revoked_role_changed",
                                target["username"], "revoked_assignments=" + str(revoked_count), school_id,
                            )
                    audit_for_school(connection, "staff_role_changed", target["username"],
                                     "Role changed from " + target["role"] + " to " + role, school_id)
                    connection.commit()
                    flash("Staff role updated.", "success")
                else:
                    abort(400)
            finally:
                connection.close()
            return redirect(url_for("staff_management"))

        connection = get_db_connection()
        try:
            class_levels = get_school_class_levels(connection, school_id)
            subjects = get_school_subjects(connection, school_id)
            users = connection.execute(
                """SELECT id, username, full_name, role, status, created_at, approved_at
                   FROM users
                   WHERE school_id = ? AND role NOT IN ('student', 'parent')
                   ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                            full_name COLLATE NOCASE, username COLLATE NOCASE""",
                (school_id,),
            ).fetchall()
            counts = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
                          SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                          SUM(CASE WHEN status='suspended' THEN 1 ELSE 0 END) AS suspended
                   FROM users WHERE school_id = ? AND role NOT IN ('student', 'parent')""",
                (school_id,),
            ).fetchone()
            school = connection.execute("SELECT name FROM schools WHERE id = ?", (school_id,)).fetchone()
            teacher_assignments = connection.execute(
                """SELECT a.id, a.class_level, a.subject, a.created_at, a.updated_at,
                          u.id AS teacher_id, u.username, u.full_name
                   FROM teacher_class_assignments a
                   JOIN users u ON u.id = a.teacher_id AND u.school_id = a.school_id
                   WHERE a.school_id = ? AND a.active = 1 AND u.role = 'teacher'
                   ORDER BY a.class_level COLLATE NOCASE, a.subject COLLATE NOCASE,
                            u.full_name COLLATE NOCASE""",
                (school_id,),
            ).fetchall()
            teachers = connection.execute(
                """SELECT id, username, full_name FROM users
                   WHERE school_id = ? AND role = 'teacher' AND status = 'active'
                     AND is_super_admin = 0
                   ORDER BY full_name COLLATE NOCASE, username COLLATE NOCASE""",
                (school_id,),
            ).fetchall()
        finally:
            connection.close()
        return render_template(
            "staff_management.html", users=users, counts=counts, school=school,
            assignable_roles={key: staff_roles[key] for key in sorted(assignable) if key in staff_roles},
            manageable_roles=manageable, can_manage_roles=current_user.role in {"super_admin", "school_admin"},
            teachers=teachers, teacher_assignments=teacher_assignments, class_levels=class_levels, subjects=subjects,
        )

    @app.route("/attendance-report", methods=["GET"])
    @login_required
    def attendance_report():
        if current_user.role not in attendance_report_roles:
            abort(403)
        school_id = require_school_context()
        today = datetime.now().date()
        default_start = today.replace(day=1).isoformat()
        start_text = request.args.get("start_date", default_start).strip()
        end_text = request.args.get("end_date", today.isoformat()).strip()
        class_filter = request.args.get("class_level", "").strip()
        try:
            start_date = datetime.strptime(start_text, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_text, "%Y-%m-%d").date()
            if start_date.isoformat() != start_text or end_date.isoformat() != end_text:
                raise ValueError
            if end_date < start_date or end_date > today or (end_date - start_date).days > 366:
                raise ValueError
        except (TypeError, ValueError):
            flash("Choose valid dates. The end date must not precede the start date, and reports can cover at most 367 days.", "danger")
            start_date = datetime.strptime(default_start, "%Y-%m-%d").date()
            end_date = today
            start_text, end_text = start_date.isoformat(), end_date.isoformat()
        connection = get_db_connection()
        try:
            classes = connection.execute(
                """SELECT DISTINCT s.class_level FROM students s
                   WHERE s.school_id = ? AND s.status = 'active'
                     AND (? <> 'teacher' OR s.class_level IN (
                         SELECT class_level FROM teacher_class_assignments
                         WHERE school_id = s.school_id AND teacher_id = ? AND active = 1
                     ))
                   ORDER BY s.class_level COLLATE NOCASE""",
                (school_id, current_user.role, int(current_user.id)),
            ).fetchall()
            rows = connection.execute(
                """SELECT s.id AS student_id, s.full_name, s.admission_number, s.class_level,
                          COUNT(a.id) AS days_recorded,
                          SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) AS present_count,
                          SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END) AS absent_count,
                          SUM(CASE WHEN a.status = 'late' THEN 1 ELSE 0 END) AS late_count,
                          SUM(CASE WHEN a.status = 'excused' THEN 1 ELSE 0 END) AS excused_count
                   FROM students s
                   LEFT JOIN attendance a
                     ON a.student_id = s.id AND a.school_id = s.school_id
                    AND a.attendance_date BETWEEN ? AND ?
                   WHERE s.school_id = ? AND s.status = 'active'
                     AND (? = '' OR s.class_level = ?)
                     AND (? <> 'teacher' OR s.class_level IN (
                         SELECT class_level FROM teacher_class_assignments
                         WHERE school_id = s.school_id AND teacher_id = ? AND active = 1
                     ))
                   GROUP BY s.id, s.full_name, s.admission_number, s.class_level
                   ORDER BY s.class_level COLLATE NOCASE, s.full_name COLLATE NOCASE""",
                (start_text, end_text, school_id, class_filter, class_filter,
                 current_user.role, int(current_user.id)),
            ).fetchall()
            totals = connection.execute(
                """SELECT COUNT(DISTINCT s.id) AS students,
                          COALESCE(SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END),0) AS present_count,
                          COALESCE(SUM(CASE WHEN a.status='absent' THEN 1 ELSE 0 END),0) AS absent_count,
                          COALESCE(SUM(CASE WHEN a.status='late' THEN 1 ELSE 0 END),0) AS late_count,
                          COALESCE(SUM(CASE WHEN a.status='excused' THEN 1 ELSE 0 END),0) AS excused_count,
                          COUNT(a.id) AS attendance_entries
                   FROM students s LEFT JOIN attendance a
                     ON a.student_id=s.id AND a.school_id=s.school_id
                    AND a.attendance_date BETWEEN ? AND ?
                   WHERE s.school_id=? AND s.status='active' AND (?='' OR s.class_level=?)
                     AND (? <> 'teacher' OR s.class_level IN (
                         SELECT class_level FROM teacher_class_assignments
                         WHERE school_id = s.school_id AND teacher_id = ? AND active = 1
                     ))""",
                (start_text, end_text, school_id, class_filter, class_filter,
                 current_user.role, int(current_user.id)),
            ).fetchone()
            daily = connection.execute(
                """SELECT a.attendance_date,
                          SUM(CASE WHEN a.status='present' THEN 1 ELSE 0 END) AS present_count,
                          SUM(CASE WHEN a.status='absent' THEN 1 ELSE 0 END) AS absent_count,
                          SUM(CASE WHEN a.status='late' THEN 1 ELSE 0 END) AS late_count,
                          SUM(CASE WHEN a.status='excused' THEN 1 ELSE 0 END) AS excused_count,
                          COUNT(*) AS total_entries
                   FROM attendance a JOIN students s
                     ON s.id=a.student_id AND s.school_id=a.school_id
                   WHERE a.school_id=? AND s.status='active'
                     AND a.attendance_date BETWEEN ? AND ?
                     AND (?='' OR s.class_level=?)
                     AND (? <> 'teacher' OR s.class_level IN (
                         SELECT class_level FROM teacher_class_assignments
                         WHERE school_id = s.school_id AND teacher_id = ? AND active = 1
                     ))
                   GROUP BY a.attendance_date ORDER BY a.attendance_date DESC""",
                (school_id, start_text, end_text, class_filter, class_filter,
                 current_user.role, int(current_user.id)),
            ).fetchall()
        finally:
            connection.close()

        enriched = []
        for row in rows:
            item = dict(row)
            item["present_count"] = item["present_count"] or 0
            item["absent_count"] = item["absent_count"] or 0
            item["late_count"] = item["late_count"] or 0
            item["excused_count"] = item["excused_count"] or 0
            item["days_recorded"] = item["days_recorded"] or 0
            denominator = item["days_recorded"]
            # Late is counted as attended; excused absences are excluded from rate.
            rate_denominator = denominator - item["excused_count"]
            item["attendance_rate"] = round(
                ((item["present_count"] + item["late_count"]) / rate_denominator) * 100, 1
            ) if rate_denominator else None
            enriched.append(item)

        if request.args.get("export", "").lower() == "csv":
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["CMHS Digital Campus - Attendance Report", school_id])
            writer.writerow(["Start date", start_text, "End date", end_text, "Class", class_filter or "All classes"])
            writer.writerow(["Student", "Admission number", "Class", "Days recorded",
                             "Present", "Absent", "Late", "Excused", "Attendance rate (%)"])
            for item in enriched:
                writer.writerow([item["full_name"], item["admission_number"], item["class_level"],
                                 item["days_recorded"], item["present_count"], item["absent_count"],
                                 item["late_count"], item["excused_count"],
                                 "" if item["attendance_rate"] is None else item["attendance_rate"]])
            response = app.response_class(output.getvalue(), mimetype="text/csv; charset=utf-8")
            response.headers["Content-Disposition"] = (
                'attachment; filename="cmhs-attendance-' + start_text + '-to-' + end_text + '.csv"'
            )
            response.headers["Cache-Control"] = "no-store, private"
            return response

        return render_template(
            "attendance_report.html", rows=enriched, totals=totals, daily=daily,
            classes=classes, start_date=start_text, end_date=end_text,
            class_filter=class_filter, today=today.isoformat(),
        )
