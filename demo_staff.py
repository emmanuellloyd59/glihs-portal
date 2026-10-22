"""Safe one-time provisioning of fictional school staff accounts."""
import secrets

def register_demo_staff_routes(ns):
    app=ns["app"]; login_required=ns["login_required"]; current_user=ns["current_user"]
    abort=ns["abort"]; request=ns["request"]; render_template=ns["render_template"]
    get_db_connection=ns["get_db_connection"]; require_school_context=ns["require_school_context"]
    generate_password_hash=ns["generate_password_hash"]
    @app.route("/staff/demo-team", methods=["POST"])
    @login_required
    def create_demo_staff_team():
        if current_user.role != "super_admin": abort(403)
        if request.form.get("confirm_demo_team") != "yes": abort(400)
        school_id=require_school_context()
        people=[
        ("cmhs_principal_demo","Dr. Grace Cooper (Demo Principal)","principal","Leadership"),
        ("cmhs_viceprincipal_demo","Mr. Daniel Morris (Demo Vice Principal)","vice_principal","Leadership"),
        ("cmhs_registrar_demo","Ms. Sarah Johnson (Demo Registrar)","registrar","Administration"),
        ("cmhs_admissions_demo","Mr. Peter Wilson (Demo Admissions Officer)","admissions_officer","Administration"),
        ("cmhs_academic_demo","Dr. Ruth Davis (Demo Academic Administrator)","academic_admin","Academics"),
        ("cmhs_attendance_demo","Ms. Mary Brown (Demo Attendance Officer)","attendance_officer","Operations"),
        ("cmhs_finance_demo","Mr. James Carter (Demo Finance Officer)","finance_admin","Finance"),
        ("cmhs_librarian_demo","Ms. Linda White (Demo Librarian)","librarian","Learning Resources"),
        ("cmhs_counselor_demo","Ms. Helen Green (Demo Counselor)","counselor","Student Support"),
        ("cmhs_teacher_english_demo","Mr. Michael Reed — English (Demo Teacher)","teacher","English"),
        ("cmhs_teacher_math_demo","Ms. Olivia Hall — Mathematics (Demo Teacher)","teacher","Mathematics"),
        ("cmhs_teacher_biology_demo","Mr. David Young — Biology (Demo Teacher)","teacher","Biology"),
        ("cmhs_teacher_chemistry_demo","Ms. Emily King — Chemistry (Demo Teacher)","teacher","Chemistry"),
        ("cmhs_teacher_physics_demo","Mr. Joseph Wright — Physics (Demo Teacher)","teacher","Physics"),
        ("cmhs_teacher_social_demo","Ms. Anna Scott — Social Studies (Demo Teacher)","teacher","Social Studies"),
        ("cmhs_teacher_computer_demo","Mr. Samuel Baker — Computer Studies (Demo Teacher)","teacher","Computer Studies"),
        ("cmhs_teacher_french_demo","Ms. Rebecca Adams — French (Demo Teacher)","teacher","French"),
        ("cmhs_teacher_music_demo","Mr. Thomas Lewis — Music (Demo Teacher)","teacher","Music"),
        ("cmhs_teacher_pe_demo","Ms. Patricia Clark — Physical Education (Demo Teacher)","teacher","Physical Education")]
        db=get_db_connection(); created=[]; existing=[]
        try:
            school=db.execute("SELECT name FROM schools WHERE id=? AND status='active'",(school_id,)).fetchone()
            if not school: abort(403)
            for username,full_name,role,subject in people:
                old=db.execute("SELECT role,status FROM users WHERE username=? AND school_id=?",(username,school_id)).fetchone()
                if old:
                    existing.append({"username":username,"role":old["role"],"status":old["status"]}); continue
                password=secrets.token_urlsafe(15)
                db.execute("""INSERT INTO users (username,full_name,password_hash,role,status,is_super_admin,school_id,approved_by,approved_at)
                    VALUES (?,?,?,?, 'active',0,?,?,CURRENT_TIMESTAMP)""",
                    (username,full_name,generate_password_hash(password),role,school_id,int(current_user.id)))
                db.execute("""INSERT INTO audit_log (actor_user_id,action,target_username,details,school_id) VALUES (?,?,?,?,?)""",
                    (int(current_user.id),"demo_staff_account_created",username,"Fictional role: "+role+"; subject/group: "+subject,school_id))
                created.append({"username":username,"full_name":full_name,"role":role,"subject":subject,"password":password})
            db.commit(); school_name=school["name"]
        except Exception:
            db.rollback(); raise
        finally: db.close()
        return render_template("staff_demo_credentials.html",created=created,existing=existing,school_name=school_name)
