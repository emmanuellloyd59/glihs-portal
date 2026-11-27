"""Tenant-scoped staff records, employment lifecycle, and private documents.

This module deliberately keeps payroll, leave balances, and performance workflows
out of the initial staff foundation. Existing users remain the identity source.
"""
import hashlib
import os
import uuid
from datetime import date
from pathlib import Path

from werkzeug.utils import secure_filename


STAFF_RECORD_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    membership_status TEXT NOT NULL DEFAULT 'active'
        CHECK (membership_status IN ('invited','active','suspended','inactive')),
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(school_id,user_id),
    UNIQUE(school_id,id)
);
CREATE TABLE IF NOT EXISTS staff_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    membership_id INTEGER NOT NULL,
    employee_number TEXT,
    preferred_name TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    emergency_contact_name TEXT,
    emergency_contact_phone TEXT,
    date_of_birth TEXT,
    gender TEXT,
    profile_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(school_id,membership_id),
    UNIQUE(school_id,employee_number),
    FOREIGN KEY(school_id,membership_id) REFERENCES staff_memberships(school_id,id)
);
CREATE TABLE IF NOT EXISTS staff_departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    manager_membership_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(school_id,name),
    UNIQUE(school_id,id),
    FOREIGN KEY(school_id,manager_membership_id) REFERENCES staff_memberships(school_id,id)
);
CREATE TABLE IF NOT EXISTS staff_employment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    membership_id INTEGER NOT NULL,
    department_id INTEGER,
    job_title TEXT NOT NULL DEFAULT '',
    employment_type TEXT NOT NULL DEFAULT 'full_time'
        CHECK(employment_type IN ('full_time','part_time','contract','temporary','volunteer','intern','other')),
    employment_status TEXT NOT NULL DEFAULT 'active'
        CHECK(employment_status IN ('draft','active','on_leave','suspended','terminated','retired')),
    start_date TEXT,
    end_date TEXT,
    supervisor_membership_id INTEGER,
    work_location TEXT,
    responsibilities_summary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(school_id,membership_id),
    FOREIGN KEY(school_id,membership_id) REFERENCES staff_memberships(school_id,id),
    FOREIGN KEY(school_id,department_id) REFERENCES staff_departments(school_id,id),
    FOREIGN KEY(school_id,supervisor_membership_id) REFERENCES staff_memberships(school_id,id),
    CHECK(end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE TABLE IF NOT EXISTS staff_qualifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    membership_id INTEGER NOT NULL,
    qualification TEXT NOT NULL,
    institution TEXT NOT NULL DEFAULT '',
    field_of_study TEXT NOT NULL DEFAULT '',
    award_date TEXT,
    verification_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK(verification_status IN ('unverified','pending','verified','rejected')),
    notes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(school_id,membership_id) REFERENCES staff_memberships(school_id,id)
);
CREATE TABLE IF NOT EXISTS staff_responsibilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    membership_id INTEGER NOT NULL,
    responsibility_type TEXT NOT NULL
        CHECK(responsibility_type IN ('class','subject','department','leadership','administrative','other')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    class_label TEXT,
    subject_id INTEGER REFERENCES subjects(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    effective_from TEXT,
    effective_to TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(school_id,membership_id) REFERENCES staff_memberships(school_id,id),
    CHECK(effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);
CREATE TABLE IF NOT EXISTS staff_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    membership_id INTEGER NOT NULL,
    document_type TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    file_size INTEGER NOT NULL CHECK(file_size > 0 AND file_size <= 5242880),
    sha256 TEXT NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    FOREIGN KEY(school_id,membership_id) REFERENCES staff_memberships(school_id,id)
);
CREATE TABLE IF NOT EXISTS staff_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    membership_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_date TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    actor_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(school_id,membership_id) REFERENCES staff_memberships(school_id,id)
);
CREATE INDEX IF NOT EXISTS idx_staff_memberships_school_status
    ON staff_memberships(school_id,membership_status);
CREATE INDEX IF NOT EXISTS idx_staff_employment_school_department
    ON staff_employment(school_id,department_id,employment_status);
CREATE INDEX IF NOT EXISTS idx_staff_qualifications_member
    ON staff_qualifications(school_id,membership_id,active);
CREATE INDEX IF NOT EXISTS idx_staff_responsibilities_member
    ON staff_responsibilities(school_id,membership_id,active);
CREATE INDEX IF NOT EXISTS idx_staff_documents_member
    ON staff_documents(school_id,membership_id,archived_at);
CREATE INDEX IF NOT EXISTS idx_staff_lifecycle_member
    ON staff_lifecycle_events(school_id,membership_id,event_date);
"""


def initialize_staff_records(get_db_connection):
    """Create the additive staff schema and backfill existing school staff safely."""
    connection = get_db_connection()
    try:
        connection.executescript(STAFF_RECORD_SCHEMA)
        # SQLite composite foreign keys need a declared unique parent key. The
        # table already has UNIQUE(school_id,id); these triggers protect identity
        # ownership as users.school_id is the canonical tenant membership.
        connection.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_staff_membership_user_tenant_insert
        BEFORE INSERT ON staff_memberships
        WHEN NOT EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.user_id AND u.school_id=NEW.school_id
                         AND u.role NOT IN ('student','parent','super_admin') AND u.is_super_admin=0)
        BEGIN SELECT RAISE(ABORT,'staff identity must belong to the same school'); END;
        CREATE TRIGGER IF NOT EXISTS trg_staff_membership_user_tenant_update
        BEFORE UPDATE OF school_id,user_id ON staff_memberships
        WHEN NOT EXISTS (SELECT 1 FROM users u WHERE u.id=NEW.user_id AND u.school_id=NEW.school_id
                         AND u.role NOT IN ('student','parent','super_admin') AND u.is_super_admin=0)
        BEGIN SELECT RAISE(ABORT,'staff identity must belong to the same school'); END;
        CREATE TRIGGER IF NOT EXISTS trg_staff_lifecycle_no_update
        BEFORE UPDATE ON staff_lifecycle_events
        BEGIN SELECT RAISE(ABORT,'staff lifecycle history is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS trg_staff_lifecycle_no_delete
        BEFORE DELETE ON staff_lifecycle_events
        BEGIN SELECT RAISE(ABORT,'staff lifecycle history is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS trg_staff_user_school_change_guard
        BEFORE UPDATE OF school_id ON users
        WHEN EXISTS (SELECT 1 FROM staff_memberships m WHERE m.user_id=OLD.id AND m.school_id<>NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'cannot move a staff identity across school memberships'); END;
        CREATE TRIGGER IF NOT EXISTS trg_staff_responsibility_subject_tenant
        BEFORE INSERT ON staff_responsibilities
        WHEN NEW.subject_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM subjects s WHERE s.id=NEW.subject_id AND s.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'subject must belong to the same school'); END;
        """)
        # Existing teacher/employee accounts are linked, not recreated. Password
        # hashes, IDs, roles, and account status are untouched.
        existing = connection.execute("""
            SELECT id,school_id,role,status,full_name FROM users
            WHERE school_id IS NOT NULL AND is_super_admin=0
              AND role NOT IN ('student','parent','super_admin')
        """).fetchall()
        for user in existing:
            status = "active" if user["status"] == "active" else "suspended" if user["status"] == "suspended" else "invited"
            connection.execute("""
                INSERT OR IGNORE INTO staff_memberships(school_id,user_id,membership_status)
                VALUES(?,?,?)
            """, (user["school_id"], user["id"], status))
            membership = connection.execute(
                "SELECT id FROM staff_memberships WHERE school_id=? AND user_id=?",
                (user["school_id"], user["id"]),
            ).fetchone()
            connection.execute("""
                INSERT OR IGNORE INTO staff_profiles(school_id,membership_id)
                VALUES(?,?)
            """, (user["school_id"], membership["id"]))
            employment_status = "active" if user["status"] == "active" else "suspended" if user["status"] == "suspended" else "draft"
            connection.execute("""
                INSERT OR IGNORE INTO staff_employment(school_id,membership_id,job_title,employment_status)
                VALUES(?,?,?,?)
            """, (user["school_id"], membership["id"], user["role"].replace("_"," ").title(), employment_status))
        connection.commit()
    finally:
        connection.close()


def register_staff_records_routes(ns):
    app = ns["app"]
    login_required = ns["login_required"]
    current_user = ns["current_user"]
    request = ns["request"]
    redirect, url_for = ns["redirect"], ns["url_for"]
    render_template, flash, abort, send_file = ns["render_template"], ns["flash"], ns["abort"], ns["send_file"]
    get_db_connection = ns["get_db_connection"]
    require_school_context = ns["require_school_context"]
    generate_password_hash = ns["generate_password_hash"]
    base_dir = Path(ns["BASE_DIR"])
    staff_admin_roles = {"super_admin","school_admin","principal","vice_principal"}
    all_school_staff_roles = {"school_admin","principal","vice_principal","registrar","admissions_officer",
                              "academic_admin","attendance_officer","teacher","finance_admin","librarian","counselor"}
    leadership_staff_roles = {"teacher","attendance_officer","librarian","counselor","registrar"}
    allowed_extensions = {"pdf","png","jpg","jpeg"}

    def manageable_staff_roles():
        if current_user.role in {"super_admin","school_admin"}:
            return all_school_staff_roles
        return leadership_staff_roles

    def actor_school():
        if current_user.role not in staff_admin_roles:
            abort(403)
        return require_school_context()

    def audit(connection, school_id, action, username, details=""):
        connection.execute(
            """INSERT INTO audit_log(actor_user_id,action,target_username,details,school_id)
               VALUES(?,?,?,?,?)""",
            (int(current_user.id),action,username,details[:1000],school_id),
        )

    def get_membership(connection, school_id, user_id):
        return connection.execute("""
            SELECT m.id AS membership_id,m.membership_status,u.id AS user_id,
                   u.username,u.full_name,u.role,u.status AS account_status,
                   p.employee_number,p.preferred_name,p.phone,p.email,p.address,
                   p.emergency_contact_name,p.emergency_contact_phone,p.date_of_birth,
                   p.gender,p.profile_notes,
                   e.id AS employment_id,e.department_id,e.job_title,e.employment_type,
                   e.employment_status,e.start_date,e.end_date,e.supervisor_membership_id,
                   e.work_location,e.responsibilities_summary,d.name AS department_name
            FROM staff_memberships m
            JOIN users u ON u.id=m.user_id AND u.school_id=m.school_id
            LEFT JOIN staff_profiles p ON p.membership_id=m.id AND p.school_id=m.school_id
            LEFT JOIN staff_employment e ON e.membership_id=m.id AND e.school_id=m.school_id
            LEFT JOIN staff_departments d ON d.id=e.department_id AND d.school_id=e.school_id
            WHERE m.school_id=? AND m.user_id=?
        """,(school_id,user_id)).fetchone()

    @app.route("/staff-records/<int:user_id>", methods=["GET","POST"])
    @login_required
    def staff_record_detail(user_id):
        school_id = actor_school()
        connection = get_db_connection()
        try:
            member = get_membership(connection,school_id,user_id)
            if member is None:
                abort(404)
            manageable = manageable_staff_roles()
            if int(user_id)!=int(current_user.id) and member["role"] not in manageable:
                abort(403)
            if request.method == "POST":
                action=request.form.get("action","").strip()
                if member["role"] not in manageable and action!="update_profile":
                    abort(403)
                if action=="update_profile":
                    fields={key:request.form.get(key,"").strip() for key in (
                        "employee_number","preferred_name","phone","email","address",
                        "emergency_contact_name","emergency_contact_phone","date_of_birth","gender","profile_notes")}
                    fields["employee_number"]=fields["employee_number"] or None
                    if fields["date_of_birth"]:
                        try:
                            if date.fromisoformat(fields["date_of_birth"]).isoformat()!=fields["date_of_birth"]: raise ValueError
                        except ValueError: abort(400)
                    if any(ch not in "0123456789+()- ." for ch in fields["phone"]+fields["emergency_contact_phone"]): abort(400)
                    if (len(fields["employee_number"] or "")>40 or len(fields["preferred_name"])>120
                        or len(fields["phone"])>40 or len(fields["email"])>254
                        or len(fields["address"])>500 or len(fields["emergency_contact_name"])>120
                        or len(fields["emergency_contact_phone"])>40 or len(fields["profile_notes"])>2000
                        or fields["gender"] not in {"","female","male","non_binary","prefer_not_to_say"}
                        or (fields["email"] and ("@" not in fields["email"] or len(fields["email"])>254))):
                        abort(400)
                    duplicate_number=connection.execute(
                        "SELECT 1 FROM staff_profiles WHERE school_id=? AND employee_number=? AND membership_id<>?",
                        (school_id,fields["employee_number"],member["membership_id"]),
                    ).fetchone() if fields["employee_number"] else None
                    if duplicate_number:
                        flash("That employee number is already assigned in this school.","danger")
                        return redirect(url_for("staff_record_detail",user_id=user_id))
                    connection.execute("""
                        UPDATE staff_profiles SET employee_number=?,preferred_name=?,phone=?,email=?,
                            address=?,emergency_contact_name=?,emergency_contact_phone=?,date_of_birth=?,
                            gender=?,profile_notes=?,updated_at=CURRENT_TIMESTAMP
                        WHERE school_id=? AND membership_id=?
                    """,(*fields.values(),school_id,member["membership_id"]))
                    audit(connection,school_id,"staff_profile_updated",member["username"],"Profile fields updated")
                    connection.commit()
                    flash("Staff profile saved.","success")
                elif action=="update_employment":
                    department_text=request.form.get("department_id","").strip()
                    supervisor_text=request.form.get("supervisor_membership_id","").strip()
                    fields={key:request.form.get(key,"").strip() for key in (
                        "job_title","employment_type","employment_status","start_date","end_date",
                        "work_location","responsibilities_summary")}
                    if (len(fields["job_title"])>120 or len(fields["work_location"])>160
                        or len(fields["responsibilities_summary"])>2000
                        or fields["employment_type"] not in {"full_time","part_time","contract","temporary","volunteer","intern","other"}
                        or fields["employment_status"] not in {"draft","active","on_leave"}):
                        abort(400)
                    for key in ("start_date","end_date"):
                        if fields[key]:
                            try:
                                if date.fromisoformat(fields[key]).isoformat()!=fields[key]: raise ValueError
                            except ValueError: abort(400)
                    if fields["start_date"] and fields["end_date"] and fields["end_date"]<fields["start_date"]: abort(400)
                    department_id=int(department_text) if department_text.isdigit() else None
                    if department_text and (department_id is None or not connection.execute(
                        "SELECT 1 FROM staff_departments WHERE id=? AND school_id=? AND active=1",
                        (department_id,school_id)).fetchone()): abort(400)
                    supervisor_id=int(supervisor_text) if supervisor_text.isdigit() else None
                    if supervisor_text and (supervisor_id is None or not connection.execute(
                        "SELECT 1 FROM staff_memberships WHERE id=? AND school_id=? AND membership_status='active'",
                        (supervisor_id,school_id)).fetchone()): abort(400)
                    connection.execute("""
                        UPDATE staff_employment SET department_id=?,job_title=?,employment_type=?,
                            employment_status=?,start_date=?,end_date=?,work_location=?,
                            responsibilities_summary=?,supervisor_membership_id=?,updated_at=CURRENT_TIMESTAMP
                        WHERE school_id=? AND membership_id=?
                    """,(department_id,fields["job_title"],fields["employment_type"],fields["employment_status"],
                         fields["start_date"] or None,fields["end_date"] or None,fields["work_location"],
                         fields["responsibilities_summary"],supervisor_id,school_id,member["membership_id"]))
                    audit(connection,school_id,"staff_employment_updated",member["username"],
                          "status="+fields["employment_status"]+"; job_title="+fields["job_title"])
                    connection.commit()
                    flash("Employment information saved.","success")
                elif action=="add_qualification":
                    qualification=request.form.get("qualification","").strip()
                    institution=request.form.get("institution","").strip()
                    field=request.form.get("field_of_study","").strip()
                    award=request.form.get("award_date","").strip()
                    if not qualification or len(qualification)>180 or len(institution)>180 or len(field)>120: abort(400)
                    if award:
                        try:
                            if date.fromisoformat(award).isoformat()!=award: raise ValueError
                        except ValueError: abort(400)
                    connection.execute("""
                        INSERT INTO staff_qualifications(school_id,membership_id,qualification,institution,
                            field_of_study,award_date,notes) VALUES(?,?,?,?,?,?,?)
                    """,(school_id,member["membership_id"],qualification,institution,field,award or None,
                         request.form.get("notes","").strip()[:1000]))
                    audit(connection,school_id,"staff_qualification_added",member["username"],qualification)
                    connection.commit()
                    flash("Qualification added.","success")
                elif action=="add_responsibility":
                    kind=request.form.get("responsibility_type","other").strip()
                    title=request.form.get("title","").strip()
                    description=request.form.get("description","").strip()
                    class_label=request.form.get("class_label","").strip()
                    subject_text=request.form.get("subject_id","").strip()
                    if kind not in {"class","subject","department","leadership","administrative","other"} or not title or len(title)>160 or len(description)>1000 or len(class_label)>100: abort(400)
                    subject_id=int(subject_text) if subject_text.isdigit() else None
                    if subject_text and (subject_id is None or not connection.execute(
                        "SELECT 1 FROM subjects WHERE id=? AND school_id=? AND active=1",(subject_id,school_id)).fetchone()): abort(400)
                    connection.execute("""
                        INSERT INTO staff_responsibilities(school_id,membership_id,responsibility_type,
                            title,description,class_label,subject_id) VALUES(?,?,?,?,?,?,?)
                    """,(school_id,member["membership_id"],kind,title,description,class_label or None,subject_id))
                    audit(connection,school_id,"staff_responsibility_added",member["username"],title)
                    connection.commit()
                    flash("Responsibility added.","success")
                elif action=="create_department":
                    name=request.form.get("department_name","").strip()
                    description=request.form.get("department_description","").strip()
                    if not name or len(name)>100 or len(description)>500: abort(400)
                    connection.execute("INSERT OR IGNORE INTO staff_departments(school_id,name,description) VALUES(?,?,?)",
                                       (school_id,name,description))
                    audit(connection,school_id,"staff_department_created",member["username"],name)
                    connection.commit()
                    flash("Department created or already exists.","success")
                elif action=="onboard":
                    if member["membership_status"]=="active" and member["employment_status"]=="active":
                        flash("This staff member is already onboarded.","info")
                    else:
                        connection.execute("""
                            UPDATE staff_memberships SET membership_status='active',joined_at=COALESCE(joined_at,CURRENT_TIMESTAMP),
                                ended_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE school_id=? AND id=?
                        """,(school_id,member["membership_id"]))
                        connection.execute("""
                            UPDATE staff_employment SET employment_status='active',end_date=NULL,updated_at=CURRENT_TIMESTAMP
                            WHERE school_id=? AND membership_id=?
                        """,(school_id,member["membership_id"]))
                        connection.execute("UPDATE users SET status='active' WHERE id=? AND school_id=?",
                                           (user_id,school_id))
                        connection.execute("""
                            INSERT INTO staff_lifecycle_events(school_id,membership_id,event_type,event_date,actor_user_id)
                            VALUES(?,?,'onboarded',?,?)
                        """,(school_id,member["membership_id"],date.today().isoformat(),int(current_user.id)))
                        audit(connection,school_id,"staff_onboarded",member["username"],"Membership and access activated")
                        connection.commit()
                        flash("Staff member onboarded and school access activated.","success")
                elif action=="offboard":
                    if int(user_id)==int(current_user.id):
                        abort(403)
                    notes=request.form.get("notes","").strip()
                    if len(notes)>1000: abort(400)
                    connection.execute("""
                        UPDATE staff_memberships SET membership_status='inactive',ended_at=?,updated_at=CURRENT_TIMESTAMP
                        WHERE school_id=? AND id=?
                    """,(date.today().isoformat(),school_id,member["membership_id"]))
                    connection.execute("""
                        UPDATE staff_employment SET employment_status='terminated',end_date=?,updated_at=CURRENT_TIMESTAMP
                        WHERE school_id=? AND membership_id=?
                    """,(date.today().isoformat(),school_id,member["membership_id"]))
                    connection.execute("UPDATE users SET status='suspended' WHERE id=? AND school_id=?",
                                       (user_id,school_id))
                    connection.execute("""
                        UPDATE teacher_class_assignments SET active=0,updated_at=CURRENT_TIMESTAMP
                        WHERE school_id=? AND teacher_id=? AND active=1
                    """,(school_id,user_id))
                    connection.execute("""
                        INSERT INTO staff_lifecycle_events(school_id,membership_id,event_type,event_date,notes,actor_user_id)
                        VALUES(?,?,'offboarded',?,?,?)
                    """,(school_id,member["membership_id"],date.today().isoformat(),notes,int(current_user.id)))
                    audit(connection,school_id,"staff_offboarded",member["username"],"School access revoked")
                    connection.commit()
                    flash("Staff member offboarded. Login and active teaching assignments have been revoked.","success")
                elif action=="update_qualification_status":
                    item=request.form.get("item_id","")
                    status=request.form.get("verification_status","").strip()
                    if not item.isdigit() or status not in {"unverified","pending","verified","rejected"}: abort(400)
                    cursor=connection.execute(
                        "UPDATE staff_qualifications SET verification_status=? WHERE id=? AND school_id=? AND membership_id=? AND active=1",
                        (status,int(item),school_id,member["membership_id"]))
                    if not cursor.rowcount: abort(404)
                    audit(connection,school_id,"staff_qualification_status_changed",member["username"],"qualification_id="+item+"; status="+status)
                    connection.commit()
                    flash("Qualification verification status updated.","success")
                elif action=="archive_qualification":
                    item=request.form.get("item_id","")
                    if not item.isdigit(): abort(400)
                    connection.execute("UPDATE staff_qualifications SET active=0 WHERE id=? AND school_id=? AND membership_id=?",
                                       (int(item),school_id,member["membership_id"]))
                    audit(connection,school_id,"staff_qualification_archived",member["username"],"qualification_id="+item)
                    connection.commit()
                    flash("Qualification archived.","success")
                elif action=="archive_responsibility":
                    item=request.form.get("item_id","")
                    if not item.isdigit(): abort(400)
                    connection.execute("UPDATE staff_responsibilities SET active=0 WHERE id=? AND school_id=? AND membership_id=?",
                                       (int(item),school_id,member["membership_id"]))
                    audit(connection,school_id,"staff_responsibility_archived",member["username"],"responsibility_id="+item)
                    connection.commit()
                    flash("Responsibility archived.","success")
                elif action=="archive_document":
                    item=request.form.get("item_id","")
                    if not item.isdigit(): abort(400)
                    cursor=connection.execute(
                        "UPDATE staff_documents SET archived_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=? AND membership_id=? AND archived_at IS NULL",
                        (int(item),school_id,member["membership_id"]))
                    if not cursor.rowcount: abort(404)
                    audit(connection,school_id,"staff_document_archived",member["username"],"document_id="+item)
                    connection.commit()
                    flash("Document archived. The original file remains in private storage for retention.","success")
                elif action=="upload_document":
                    document_type=request.form.get("document_type","").strip()
                    uploaded=request.files.get("document")
                    if not document_type or len(document_type)>80 or not uploaded or not uploaded.filename: abort(400)
                    original=secure_filename(uploaded.filename)
                    extension=original.rsplit(".",1)[-1].lower() if "." in original else ""
                    if not original or extension not in allowed_extensions: abort(400)
                    content=uploaded.stream.read(5242881)
                    if not content or len(content)>5242880: abort(400)
                    signatures={"pdf":content.startswith(b"%PDF-"),"png":content.startswith(b"\x89PNG\r\n\x1a\n"),
                                "jpg":content.startswith(b"\xff\xd8\xff"),"jpeg":content.startswith(b"\xff\xd8\xff")}
                    if not signatures.get(extension,False): abort(400)
                    digest=hashlib.sha256(content).hexdigest()
                    key=str(uuid.uuid4())+"."+extension
                    storage_root=Path(ns["INSTANCE_DIR"])/"private_staff_documents"/str(school_id)
                    storage_root.mkdir(parents=True,exist_ok=True)
                    target_path=storage_root/key
                    with target_path.open("xb") as handle: handle.write(content)
                    content_type=uploaded.mimetype or "application/octet-stream"
                    try:
                        connection.execute("""
                            INSERT INTO staff_documents(school_id,membership_id,document_type,original_filename,
                                storage_key,content_type,file_size,sha256,uploaded_by)
                            VALUES(?,?,?,?,?,?,?,?,?)
                        """,(school_id,member["membership_id"],document_type,original,key,content_type,
                             len(content),digest,int(current_user.id)))
                        audit(connection,school_id,"staff_document_uploaded",member["username"],document_type)
                        connection.commit()
                    except Exception:
                        target_path.unlink(missing_ok=True)
                        raise
                    flash("Private staff document uploaded.","success")
                else:
                    abort(400)
                return redirect(url_for("staff_record_detail",user_id=user_id))
            qualifications=connection.execute("""
                SELECT * FROM staff_qualifications WHERE school_id=? AND membership_id=? AND active=1 ORDER BY created_at DESC
            """,(school_id,member["membership_id"])).fetchall()
            teacher_assignments=connection.execute("""
                SELECT id,class_level,subject,created_at FROM teacher_class_assignments
                WHERE school_id=? AND teacher_id=? AND active=1 ORDER BY class_level COLLATE NOCASE,subject COLLATE NOCASE
            """,(school_id,user_id)).fetchall()
            responsibilities=connection.execute("""
                SELECT r.*,s.name AS subject_name FROM staff_responsibilities r
                LEFT JOIN subjects s ON s.id=r.subject_id AND s.school_id=r.school_id
                WHERE r.school_id=? AND r.membership_id=? AND r.active=1 ORDER BY r.created_at DESC
            """,(school_id,member["membership_id"])).fetchall()
            documents=connection.execute("""
                SELECT id,document_type,original_filename,file_size,uploaded_at FROM staff_documents
                WHERE school_id=? AND membership_id=? AND archived_at IS NULL ORDER BY uploaded_at DESC
            """,(school_id,member["membership_id"])).fetchall()
            departments=connection.execute("SELECT id,name FROM staff_departments WHERE school_id=? AND active=1 ORDER BY name",(school_id,)).fetchall()
            subjects=connection.execute("SELECT id,name FROM subjects WHERE school_id=? AND active=1 ORDER BY name",(school_id,)).fetchall()
            supervisors=connection.execute("""
                SELECT m.id,u.full_name,u.username FROM staff_memberships m JOIN users u ON u.id=m.user_id
                WHERE m.school_id=? AND m.membership_status='active' AND m.id<>? ORDER BY u.full_name
            """,(school_id,member["membership_id"])).fetchall()
            departments_all=departments
            lifecycle=connection.execute("""
                SELECT event_type,event_date,notes,created_at FROM staff_lifecycle_events
                WHERE school_id=? AND membership_id=? ORDER BY event_date DESC,id DESC
            """,(school_id,member["membership_id"])).fetchall()
            school=connection.execute("SELECT name FROM schools WHERE id=?",(school_id,)).fetchone()
            return render_template("staff_record_detail.html",member=member,qualifications=qualifications,
                responsibilities=responsibilities,documents=documents,departments=departments_all,
                subjects=subjects,supervisors=supervisors,lifecycle=lifecycle,school=school,
                teacher_assignments=teacher_assignments)
        finally:
            connection.close()

    @app.route("/staff-records/<int:user_id>/documents/<int:document_id>")
    @login_required
    def download_staff_document(user_id,document_id):
        school_id=actor_school()
        connection=get_db_connection()
        try:
            row=connection.execute("""
                SELECT d.storage_key,d.original_filename,u.role FROM staff_documents d
                JOIN staff_memberships m ON m.id=d.membership_id AND m.school_id=d.school_id
                JOIN users u ON u.id=m.user_id AND u.school_id=m.school_id
                WHERE d.id=? AND d.school_id=? AND m.user_id=? AND d.archived_at IS NULL
            """,(document_id,school_id,user_id)).fetchone()
            if row is None or (row["role"] not in manageable_staff_roles() and int(user_id)!=int(current_user.id)): abort(404)
            path=Path(ns["INSTANCE_DIR"])/"private_staff_documents"/str(school_id)/row["storage_key"]
            if not path.is_file(): abort(404)
            return send_file(path,as_attachment=True,download_name=row["original_filename"])
        finally:
            connection.close()
