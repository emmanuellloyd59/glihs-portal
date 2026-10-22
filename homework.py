"""School-scoped homework and assignment routes for CMHS Digital Campus."""
from datetime import datetime


def register_homework_routes(ns):
    app, login_required = ns["app"], ns["login_required"]
    current_user, request = ns["current_user"], ns["request"]
    redirect, url_for = ns["redirect"], ns["url_for"]
    render_template, flash, abort = ns["render_template"], ns["flash"], ns["abort"]
    get_db_connection = ns["get_db_connection"]
    require_school_context, write_audit = ns["require_school_context"], ns["write_audit"]
    class_levels = ns["TIMETABLE_CLASS_LEVELS"]
    staff_roles = {"super_admin", "school_admin", "principal", "vice_principal", "academic_admin", "registrar", "teacher"}
    admin_roles = {"super_admin", "school_admin", "principal", "vice_principal", "academic_admin"}

    # Additive tables only; existing school and student records are not reset.
    connection = get_db_connection()
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS homework_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            class_level TEXT NOT NULL, subject TEXT NOT NULL, title TEXT NOT NULL,
            instructions TEXT NOT NULL, due_date TEXT NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','archived'))
        )""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_homework_school_class_due
            ON homework_assignments(school_id,class_level,due_date,status)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS homework_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            assignment_id INTEGER NOT NULL REFERENCES homework_assignments(id),
            student_id INTEGER NOT NULL REFERENCES students(id),
            response_text TEXT NOT NULL, submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            score REAL CHECK(score IS NULL OR (score >= 0 AND score <= 100)),
            feedback TEXT NOT NULL DEFAULT '', graded_by INTEGER REFERENCES users(id),
            graded_at TEXT, UNIQUE(assignment_id,student_id)
        )""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_homework_submissions_school_assignment
            ON homework_submissions(school_id,assignment_id,submitted_at)""")
        connection.commit()
    finally:
        connection.close()

    @app.route("/assignments", methods=["GET", "POST"])
    @login_required
    def assignments():
        role = current_user.role
        if role not in staff_roles | {"student"}:
            abort(403)
        school_id = require_school_context()
        connection = get_db_connection()
        student, submissions = None, []
        try:
            if request.method == "POST":
                action = request.form.get("action", "create").strip()
                if action == "create":
                    if role not in staff_roles:
                        abort(403)
                    level = request.form.get("class_level", "").strip()
                    subject = request.form.get("subject", "").strip()
                    title = request.form.get("title", "").strip()
                    instructions = request.form.get("instructions", "").strip()
                    due_date = request.form.get("due_date", "").strip()
                    try:
                        valid_date = datetime.strptime(due_date, "%Y-%m-%d").date().isoformat() == due_date
                    except (TypeError, ValueError):
                        valid_date = False
                    if (level not in class_levels or not subject or len(subject) > 80 or
                        not title or len(title) > 140 or not instructions or
                        len(instructions) > 10000 or not valid_date):
                        flash("Check the class, subject, title, instructions and due date.", "danger")
                        return redirect(url_for("assignments"))
                    connection.execute("""INSERT INTO homework_assignments
                        (school_id,class_level,subject,title,instructions,due_date,created_by)
                        VALUES (?,?,?,?,?,?,?)""",
                        (school_id,level,subject,title,instructions,due_date,int(current_user.id)))
                    connection.commit()
                    write_audit(current_user.id,"homework_assignment_created",title,f"class={level}; due={due_date}")
                    flash("Homework assignment published.","success")
                    return redirect(url_for("assignments"))

                if action == "submit":
                    if role != "student":
                        abort(403)
                    aid = request.form.get("assignment_id","").strip()
                    answer = request.form.get("response_text","").strip()
                    if not aid.isdigit() or not answer or len(answer) > 10000:
                        flash("Enter your assignment response before submitting.","danger")
                        return redirect(url_for("assignments"))
                    student = connection.execute("""SELECT id,class_level FROM students
                        WHERE account_user_id=? AND school_id=? AND status='active'""",
                        (int(current_user.id),school_id)).fetchone()
                    if student is None:
                        abort(403,description="Your student account must be linked to an active student record.")
                    assignment = connection.execute("""SELECT id,class_level,due_date FROM homework_assignments
                        WHERE id=? AND school_id=? AND status='published'""",(int(aid),school_id)).fetchone()
                    if assignment is None or assignment["class_level"] != student["class_level"]:
                        abort(404)
                    if assignment["due_date"] < datetime.now().date().isoformat():
                        flash("This assignment is past its due date. Contact your teacher if you need an extension.","danger")
                        return redirect(url_for("assignments"))
                    connection.execute("""INSERT INTO homework_submissions
                        (school_id,assignment_id,student_id,response_text) VALUES (?,?,?,?)
                        ON CONFLICT(assignment_id,student_id) DO UPDATE SET
                        response_text=excluded.response_text, submitted_at=CURRENT_TIMESTAMP,
                        score=NULL, feedback='', graded_by=NULL, graded_at=NULL
                        WHERE homework_submissions.school_id=excluded.school_id""",
                        (school_id,int(aid),student["id"],answer))
                    connection.commit()
                    write_audit(current_user.id,"homework_submitted",aid)
                    flash("Your homework has been submitted.","success")
                    return redirect(url_for("assignments"))

                if action == "grade":
                    if role not in staff_roles:
                        abort(403)
                    sid = request.form.get("submission_id","").strip()
                    feedback = request.form.get("feedback","").strip()
                    if not sid.isdigit() or len(feedback) > 2000:
                        flash("Check the submission and feedback.","danger")
                        return redirect(url_for("assignments"))
                    try:
                        score = float(request.form.get("score",""))
                        if not 0 <= score <= 100:
                            raise ValueError
                    except (ValueError,TypeError):
                        flash("Score must be between 0 and 100.","danger")
                        return redirect(url_for("assignments"))
                    owner_clause = " AND created_by=?" if role == "teacher" else ""
                    params = [score,feedback,int(current_user.id),int(sid),school_id,school_id]
                    if role == "teacher":
                        params.append(int(current_user.id))
                    result = connection.execute("""UPDATE homework_submissions
                        SET score=?,feedback=?,graded_by=?,graded_at=CURRENT_TIMESTAMP
                        WHERE id=? AND school_id=? AND assignment_id IN
                        (SELECT id FROM homework_assignments WHERE school_id=? AND status='published'""" +
                        owner_clause + ")",params)
                    connection.commit()
                    if result.rowcount != 1:
                        abort(404)
                    write_audit(current_user.id,"homework_submission_graded",sid,f"score={score:g}")
                    flash("Homework marked and feedback saved.","success")
                    return redirect(url_for("assignments"))
                abort(400)

            if role == "student":
                student = connection.execute("""SELECT id,class_level FROM students
                    WHERE account_user_id=? AND school_id=? AND status='active'""",
                    (int(current_user.id),school_id)).fetchone()
                if student is None:
                    assignment_rows = []
                    flash("Your student account is not linked to an active student record yet.","warning")
                else:
                    assignment_rows = connection.execute("""SELECT a.*,u.full_name AS teacher_name,
                        u.username AS teacher_username,s.id AS submission_id,s.response_text,s.submitted_at,
                        s.score,s.feedback,s.graded_at FROM homework_assignments a
                        JOIN users u ON u.id=a.created_by AND u.school_id=a.school_id
                        LEFT JOIN homework_submissions s ON s.assignment_id=a.id AND s.student_id=? AND s.school_id=a.school_id
                        WHERE a.school_id=? AND a.class_level=? AND a.status='published'
                        ORDER BY a.due_date,a.created_at DESC""",
                        (student["id"],school_id,student["class_level"])).fetchall()
            else:
                own = " AND a.created_by=?" if role == "teacher" else ""
                params = [school_id] + ([int(current_user.id)] if role == "teacher" else [])
                assignment_rows = connection.execute("""SELECT a.*,u.full_name AS teacher_name,
                    u.username AS teacher_username FROM homework_assignments a
                    JOIN users u ON u.id=a.created_by AND u.school_id=a.school_id
                    WHERE a.school_id=? AND a.status='published'""" + own +
                    " ORDER BY a.due_date,a.created_at DESC",params).fetchall()
                own_submission = " AND a.created_by=?" if role == "teacher" else ""
                sub_params = (school_id,int(current_user.id)) if role == "teacher" else (school_id,)
                submissions = connection.execute("""SELECT s.id AS submission_id,s.assignment_id,
                    s.response_text,s.submitted_at,s.score,s.feedback,s.graded_at,
                    st.full_name AS student_name,st.admission_number,st.class_level,
                    a.title AS assignment_title FROM homework_submissions s
                    JOIN homework_assignments a ON a.id=s.assignment_id AND a.school_id=s.school_id
                    JOIN students st ON st.id=s.student_id AND st.school_id=s.school_id
                    WHERE s.school_id=? AND a.status='published'""" + own_submission +
                    " ORDER BY s.submitted_at DESC",sub_params).fetchall()
        finally:
            connection.close()
        return render_template("assignments.html",assignments=assignment_rows,
            submissions=submissions,student=student,role=role,can_create=role in staff_roles,
            can_grade=role in staff_roles,class_levels=class_levels,
            today=datetime.now().date().isoformat())
