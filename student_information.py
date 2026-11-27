"""Tenant-aware Student Information Management System APIs.

The module extends the legacy students table without replacing or deleting it.
Every resource is school-scoped in SQL and independently checked by authorization.
"""
import csv
import hashlib
import io
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename


ADMISSION_STATES = {"draft", "submitted", "under_review", "accepted", "rejected", "waitlisted", "converted"}
TRANSFER_STATES = {"requested", "approved", "completed", "rejected", "cancelled"}
DISCIPLINE_LEVELS = {"low", "medium", "high", "critical"}
DOCUMENT_TYPES = {"identity", "birth_certificate", "previous_school", "medical", "consent", "photo", "other"}
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
IDENTIFIER_DEFAULT = "STU-{year}-{sequence:05d}"


def initialize_student_information_schema(connection_factory):
    """Add student-management tables and tenant-scoped identifier constraints."""
    db = connection_factory()
    try:
        db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS student_identifier_config (
          school_id INTEGER PRIMARY KEY REFERENCES schools(id),
          pattern TEXT NOT NULL DEFAULT 'STU-{year}-{sequence:05d}',
          next_sequence INTEGER NOT NULL DEFAULT 1 CHECK(next_sequence > 0),
          updated_by INTEGER REFERENCES users(id),
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS student_admissions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER REFERENCES students(id),
          application_number TEXT NOT NULL,
          applicant_name TEXT NOT NULL,
          date_of_birth TEXT,
          requested_class TEXT NOT NULL,
          guardian_name TEXT,
          guardian_email TEXT,
          guardian_phone TEXT,
          previous_school TEXT,
          status TEXT NOT NULL DEFAULT 'submitted'
            CHECK(status IN ('draft','submitted','under_review','accepted','rejected','waitlisted','converted')),
          application_data_json TEXT NOT NULL DEFAULT '{}',
          submitted_at TEXT,
          reviewed_by INTEGER REFERENCES users(id),
          reviewed_at TEXT,
          review_note TEXT,
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id, application_number)
        );
        CREATE INDEX IF NOT EXISTS idx_student_admissions_school_status
          ON student_admissions(school_id,status,created_at);
        CREATE TABLE IF NOT EXISTS student_guardians (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          full_name TEXT NOT NULL,
          relationship TEXT NOT NULL,
          phone TEXT,
          email TEXT,
          address TEXT,
          occupation TEXT,
          parent_user_id INTEGER REFERENCES users(id),
          is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
          is_emergency_contact INTEGER NOT NULL DEFAULT 0 CHECK(is_emergency_contact IN (0,1)),
          verified_at TEXT,
          consent_status TEXT NOT NULL DEFAULT 'unknown'
            CHECK(consent_status IN ('unknown','pending','granted','withdrawn')),
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_student_guardians_school_student
          ON student_guardians(school_id,student_id);
        CREATE TABLE IF NOT EXISTS student_academic_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          institution_name TEXT NOT NULL,
          academic_year TEXT,
          class_level TEXT,
          result_summary TEXT,
          start_date TEXT,
          end_date TEXT,
          verified INTEGER NOT NULL DEFAULT 0 CHECK(verified IN (0,1)),
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS student_transfers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          transfer_type TEXT NOT NULL CHECK(transfer_type IN ('inbound','outbound','campus')),
          destination_name TEXT,
          destination_school_id INTEGER REFERENCES schools(id),
          effective_date TEXT NOT NULL,
          reason TEXT,
          status TEXT NOT NULL DEFAULT 'requested'
            CHECK(status IN ('requested','approved','completed','rejected','cancelled')),
          approved_by INTEGER REFERENCES users(id),
          approved_at TEXT,
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS student_withdrawals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          effective_date TEXT NOT NULL,
          reason_code TEXT NOT NULL,
          notes TEXT,
          destination_name TEXT,
          approved_by INTEGER REFERENCES users(id),
          created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS student_disciplinary_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          incident_date TEXT NOT NULL,
          category TEXT NOT NULL,
          severity TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
          description TEXT NOT NULL,
          action_taken TEXT,
          follow_up_date TEXT,
          confidential INTEGER NOT NULL DEFAULT 1 CHECK(confidential IN (0,1)),
          recorded_by INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_student_discipline_school_student
          ON student_disciplinary_records(school_id,student_id,incident_date);
        CREATE TABLE IF NOT EXISTS student_documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          document_type TEXT NOT NULL CHECK(document_type IN
            ('identity','birth_certificate','previous_school','medical','consent','photo','other')),
          original_name TEXT NOT NULL,
          stored_name TEXT NOT NULL UNIQUE,
          mime_type TEXT NOT NULL,
          file_size INTEGER NOT NULL CHECK(file_size > 0),
          sha256 TEXT NOT NULL,
          uploaded_by INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_student_documents_school_student
          ON student_documents(school_id,student_id,archived_at);
        """)
        # Existing student IDs are retained. Uniqueness becomes school-scoped,
        # matching the tenant model and allowing independent schools to use the
        # same identifier format. Refuse ambiguous duplicates before changing it.
        duplicates = db.execute("""
          SELECT school_id,lower(student_id_number) AS value,COUNT(*) AS n
          FROM students WHERE student_id_number IS NOT NULL AND trim(student_id_number)<>''
          GROUP BY school_id,lower(student_id_number) HAVING COUNT(*)>1 LIMIT 1
        """).fetchone()
        admission_duplicates = db.execute("""
          SELECT school_id,lower(admission_number) AS value,COUNT(*) AS n
          FROM students GROUP BY school_id,lower(admission_number) HAVING COUNT(*)>1 LIMIT 1
        """).fetchone()
        if duplicates or admission_duplicates:
            raise RuntimeError("Cannot migrate student identifiers: duplicate value within a school needs manual review.")
        db.execute("DROP INDEX IF EXISTS idx_students_student_id_number")
        db.execute("""
          CREATE UNIQUE INDEX IF NOT EXISTS idx_students_school_identifier
          ON students(school_id,student_id_number COLLATE NOCASE)
          WHERE student_id_number IS NOT NULL
        """)
        db.execute("""
          CREATE UNIQUE INDEX IF NOT EXISTS idx_students_school_admission
          ON students(school_id,admission_number COLLATE NOCASE)
        """)
        guardian_columns = {row["name"] for row in db.execute("PRAGMA table_info(student_guardians)").fetchall()}
        if "archived_at" not in guardian_columns:
            db.execute("ALTER TABLE student_guardians ADD COLUMN archived_at TEXT")
        # Tenant integrity is also enforced at the database boundary for all
        # related student records, not just by route-level checks.
        for table in ("student_guardians","student_academic_history","student_transfers",
                      "student_withdrawals","student_disciplinary_records","student_documents"):
            for operation in ("INSERT","UPDATE"):
                trigger = f"trg_{table}_school_{operation.lower()}"
                db.execute(f"""
                  CREATE TRIGGER IF NOT EXISTS {trigger}
                  BEFORE {operation} ON {table}
                  WHEN NOT EXISTS (
                    SELECT 1 FROM students s
                    WHERE s.id=NEW.student_id AND s.school_id=NEW.school_id
                  )
                  BEGIN SELECT RAISE(ABORT,'student tenant mismatch'); END
                """)
        for operation in ("INSERT","UPDATE"):
            trigger = f"trg_student_admissions_school_{operation.lower()}"
            db.execute(f"""
              CREATE TRIGGER IF NOT EXISTS {trigger}
              BEFORE {operation} ON student_admissions
              WHEN NEW.student_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM students s WHERE s.id=NEW.student_id AND s.school_id=NEW.school_id
              )
              BEGIN SELECT RAISE(ABORT,'admission tenant mismatch'); END
            """)
        db.execute("""
          INSERT OR IGNORE INTO student_identifier_config(school_id,pattern,next_sequence)
          SELECT id,?,1 FROM schools
        """, (IDENTIFIER_DEFAULT,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _clean(value, limit=500, required=False):
    value = str(value or "").strip()
    if required and not value:
        raise ValueError("A required field is missing.")
    if len(value) > limit or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ValueError("A field contains invalid characters or exceeds its length limit.")
    return value


def _valid_date(value, required=False):
    value = _clean(value, 10, required)
    if not value:
        return None
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD.")
    return value


def _json_rows(rows):
    return [dict(row) for row in rows]


def allocate_student_identifier(connection, school_id):
    """Allocate the next configured identifier inside the caller's transaction."""
    connection.execute(
        "INSERT OR IGNORE INTO student_identifier_config(school_id,pattern,next_sequence) VALUES(?,?,1)",
        (int(school_id), IDENTIFIER_DEFAULT),
    )
    row = connection.execute(
        "SELECT pattern,next_sequence FROM student_identifier_config WHERE school_id=?",
        (int(school_id),),
    ).fetchone()
    sequence = int(row["next_sequence"])
    identifier = row["pattern"].format(year=date.today().year, sequence=sequence)
    connection.execute(
        "UPDATE student_identifier_config SET next_sequence=?,updated_at=CURRENT_TIMESTAMP WHERE school_id=?",
        (sequence + 1, int(school_id)),
    )
    return identifier


def create_student_information_blueprint(connection_factory, instance_path, school_context_resolver):
    bp = Blueprint("student_information", __name__)
    storage_root = Path(instance_path) / "student_documents"

    def school_context(permission):
        from authorization import require_permission
        school_id = school_context_resolver()
        require_permission(permission, connection_factory, school_id=int(school_id))
        return int(school_id)

    def student_or_404(db, school_id, student_id):
        row = db.execute("SELECT * FROM students WHERE id=? AND school_id=?",
                         (student_id, school_id)).fetchone()
        if not row:
            abort(404)
        return row

    def audit(db, school_id, action, student_id, details=""):
        db.execute("""
          INSERT INTO audit_log(actor_user_id,action,target_username,details,school_id)
          VALUES(?,?,?,?,?)
        """, (int(current_user.id), action, str(student_id), details[:500], school_id))

    def sensitive_allowed(db, school_id):
        from authorization import has_permission
        return has_permission(db, "student.view_sensitive", school_id=school_id)

    @bp.get("/api/v1/students")
    @login_required
    def search_students():
        school_id = school_context("student.view_records")
        query = _clean(request.args.get("q"), 100)
        class_level = _clean(request.args.get("class_level"), 50)
        status = _clean(request.args.get("status"), 30)
        page = max(request.args.get("page", 1, type=int) or 1, 1)
        per_page = min(max(request.args.get("per_page", 25, type=int) or 25, 1), 100)
        clauses, params = ["school_id=?"], [school_id]
        if query:
            clauses.append("(full_name LIKE ? OR admission_number LIKE ? OR student_id_number LIKE ?)")
            params.extend([f"%{query}%"] * 3)
        if class_level:
            clauses.append("class_level=?"); params.append(class_level)
        if status:
            clauses.append("status=?"); params.append(status)
        where = " AND ".join(clauses)
        db = connection_factory()
        try:
            total = db.execute(f"SELECT COUNT(*) FROM students WHERE {where}", params).fetchone()[0]
            rows = db.execute(f"""
              SELECT id,student_id_number,admission_number,full_name,class_level,status,enrollment_date
              FROM students WHERE {where} ORDER BY full_name COLLATE NOCASE,id LIMIT ? OFFSET ?
            """, [*params, per_page, (page-1)*per_page]).fetchall()
            return jsonify({"items": _json_rows(rows), "page": page, "per_page": per_page, "total": total})
        finally:
            db.close()

    @bp.get("/api/v1/students/<int:student_id>")
    @login_required
    def get_student(student_id):
        school_id = school_context("student.view_records")
        db = connection_factory()
        try:
            student = student_or_404(db, school_id, student_id)
            result = dict(student)
            if not sensitive_allowed(db, school_id):
                for field in ("date_of_birth","guardian_name","guardian_phone","emergency_contact_name",
                              "emergency_contact_phone","record_notes"):
                    result.pop(field, None)
            result.pop("account_user_id", None)
            if not sensitive_allowed(db, school_id):
                result.pop("previous_school", None)
            return jsonify({"student": result})
        finally:
            db.close()

    @bp.get("/api/v1/students/identifier-config")
    @login_required
    def get_identifier_config():
        school_id = school_context("student.view_records")
        db = connection_factory()
        try:
            row = db.execute("SELECT pattern,next_sequence FROM student_identifier_config WHERE school_id=?",
                             (school_id,)).fetchone()
            return jsonify(dict(row) if row else {"pattern": IDENTIFIER_DEFAULT, "next_sequence": 1})
        finally:
            db.close()

    @bp.put("/api/v1/students/identifier-config")
    @login_required
    def set_identifier_config():
        school_id = school_context("student.manage_records")
        payload = request.get_json(silent=True) or {}
        pattern = _clean(payload.get("pattern"), 80, True)
        if not re.fullmatch(r"[A-Za-z0-9_{}:.-]+", pattern) or "{sequence" not in pattern or "{year}" not in pattern:
            return jsonify({"error": "Pattern must include {year} and {sequence}, using only letters, numbers and safe separators."}), 400
        try:
            pattern.format(year=2026, sequence=1)
        except (KeyError, ValueError):
            return jsonify({"error": "Unsupported identifier pattern."}), 400
        db = connection_factory()
        try:
            db.execute("""
              INSERT INTO student_identifier_config(school_id,pattern,next_sequence,updated_by)
              VALUES(?,?,1,?) ON CONFLICT(school_id) DO UPDATE SET
                pattern=excluded.pattern,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP
            """, (school_id, pattern, int(current_user.id)))
            db.commit()
            audit(db, school_id, "student_identifier_pattern_updated", "school", "pattern changed")
            db.commit()
            return jsonify({"school_id": school_id, "pattern": pattern})
        finally:
            db.close()

    @bp.get("/api/v1/students/duplicate-check")
    @login_required
    def duplicate_check():
        school_id=school_context("student.view_records")
        name=_clean(request.args.get("full_name"),150,True)
        dob=_valid_date(request.args.get("date_of_birth"))
        if not dob:
            return jsonify({"error":"Date of birth is required for a duplicate check."}),400
        db=connection_factory()
        try:
            rows=db.execute("""SELECT id,student_id_number,admission_number,full_name,class_level,status
              FROM students WHERE school_id=? AND lower(trim(full_name))=lower(trim(?))
                AND date_of_birth=? ORDER BY id LIMIT 10""",(school_id,name,dob)).fetchall()
            applications=db.execute("""SELECT id,application_number,applicant_name,requested_class,status
              FROM student_admissions WHERE school_id=? AND lower(trim(applicant_name))=lower(trim(?))
                AND date_of_birth=? ORDER BY id DESC LIMIT 10""",(school_id,name,dob)).fetchall()
            return jsonify({"possible_student_matches":_json_rows(rows),
                            "possible_application_matches":_json_rows(applications),
                            "requires_manual_identity_review":bool(rows or applications)})
        finally: db.close()

    @bp.post("/api/v1/admissions")
    @login_required
    def create_admission():
        school_id = school_context("student.manage_admissions")
        payload = request.get_json(silent=True) or {}
        try:
            name = _clean(payload.get("applicant_name"), 150, True)
            requested_class = _clean(payload.get("requested_class"), 50, True)
            application_number = _clean(payload.get("application_number"), 50, True)
            dob = _valid_date(payload.get("date_of_birth"))
            state = payload.get("status", "submitted")
            if state not in {"draft","submitted"}:
                raise ValueError("New applications must start as draft or submitted.")
            extra = payload.get("application_data", {})
            if not isinstance(extra, dict) or len(json.dumps(extra)) > 8000:
                raise ValueError("Application data must be a small JSON object.")
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        db = connection_factory()
        try:
            if dob:
                matches = db.execute("""SELECT id,full_name,admission_number FROM students
                  WHERE school_id=? AND lower(trim(full_name))=lower(trim(?)) AND date_of_birth=?
                  ORDER BY id LIMIT 5""", (school_id,name,dob)).fetchall()
                applications = db.execute("""SELECT id,application_number,applicant_name FROM student_admissions
                  WHERE school_id=? AND lower(trim(applicant_name))=lower(trim(?)) AND date_of_birth=?
                  ORDER BY id DESC LIMIT 5""", (school_id,name,dob)).fetchall()
                override = _clean(payload.get("duplicate_override_reason"),500)
                if (matches or applications) and len(override) < 10:
                    return jsonify({"error":"Possible duplicate applicant found. Review matching records before continuing.",
                                    "student_matches":_json_rows(matches),
                                    "application_matches":_json_rows(applications),
                                    "override_required":True}),409
            cur = db.execute("""
              INSERT INTO student_admissions
              (school_id,application_number,applicant_name,date_of_birth,requested_class,
               guardian_name,guardian_email,guardian_phone,previous_school,status,
               application_data_json,submitted_at,created_by)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (school_id, application_number, name, dob, requested_class,
                  _clean(payload.get("guardian_name"),150), _clean(payload.get("guardian_email"),254),
                  _clean(payload.get("guardian_phone"),40), _clean(payload.get("previous_school"),150),
                  state, json.dumps(extra, sort_keys=True), datetime.now(timezone.utc).isoformat()
                  if state == "submitted" else None, int(current_user.id)))
            audit(db, school_id, "student_admission_created", cur.lastrowid, state)
            db.commit()
            return jsonify({"admission_id": cur.lastrowid, "application_number": application_number, "status": state}), 201
        except Exception as exc:
            db.rollback()
            if "UNIQUE" in str(exc):
                return jsonify({"error": "Application number already exists in this school."}), 409
            raise
        finally:
            db.close()

    @bp.get("/api/v1/admissions")
    @login_required
    def list_admissions():
        school_id = school_context("student.view_records")
        status = _clean(request.args.get("status"), 30)
        db = connection_factory()
        try:
            if status and status not in ADMISSION_STATES:
                return jsonify({"error": "Invalid admission status."}), 400
            sql = "SELECT id,application_number,applicant_name,requested_class,status,created_at,submitted_at FROM student_admissions WHERE school_id=?"
            params = [school_id]
            if status:
                sql += " AND status=?"; params.append(status)
            rows = db.execute(sql + " ORDER BY created_at DESC,id DESC LIMIT 200", params).fetchall()
            return jsonify({"items": _json_rows(rows)})
        finally:
            db.close()

    @bp.post("/api/v1/admissions/<int:admission_id>/review")
    @login_required
    def review_admission(admission_id):
        school_id = school_context("student.manage_admissions")
        payload = request.get_json(silent=True) or {}
        state = payload.get("status")
        if state not in {"under_review","accepted","rejected","waitlisted"}:
            return jsonify({"error": "Invalid review status."}), 400
        db = connection_factory()
        try:
            cur = db.execute("""
              UPDATE student_admissions SET status=?,reviewed_by=?,reviewed_at=?,review_note=?,
                updated_at=CURRENT_TIMESTAMP
              WHERE id=? AND school_id=? AND status IN ('submitted','under_review','accepted','waitlisted')
            """, (state, int(current_user.id), datetime.now(timezone.utc).isoformat(),
                  _clean(payload.get("review_note"),1000), admission_id, school_id))
            if not cur.rowcount:
                abort(404)
            audit(db, school_id, "student_admission_reviewed", admission_id, state)
            db.commit()
            return jsonify({"admission_id": admission_id, "status": state})
        finally:
            db.close()

    @bp.post("/api/v1/admissions/<int:admission_id>/convert")
    @login_required
    def convert_admission(admission_id):
        school_id = school_context("student.manage_records")
        payload = request.get_json(silent=True) or {}
        admission_number = _clean(payload.get("admission_number"), 50, True)
        class_level = _clean(payload.get("class_level"), 50, True)
        db = connection_factory()
        try:
            admission = db.execute("SELECT * FROM student_admissions WHERE id=? AND school_id=?",
                                   (admission_id, school_id)).fetchone()
            if not admission or admission["status"] != "accepted" or admission["student_id"] is not None:
                abort(404)
            identifier = allocate_student_identifier(db, school_id)
            cur = db.execute("""
              INSERT INTO students(admission_number,full_name,class_level,date_of_birth,
                guardian_name,guardian_phone,previous_school,school_id,status)
              VALUES(?,?,?,?,?,?,?,?, 'active')
            """, (admission_number, admission["applicant_name"], class_level,
                  admission["date_of_birth"], admission["guardian_name"],
                  admission["guardian_phone"], admission["previous_school"], school_id))
            db.execute("UPDATE students SET student_id_number=? WHERE id=? AND school_id=?",
                       (identifier, cur.lastrowid, school_id))
            db.execute("UPDATE student_admissions SET student_id=?,status='converted',updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?",
                       (cur.lastrowid, admission_id, school_id))
            audit(db, school_id, "student_admission_converted", cur.lastrowid, admission_number)
            db.commit()
            return jsonify({"student_id": cur.lastrowid, "student_identifier": identifier, "status": "converted"}), 201
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @bp.post("/api/v1/students/<int:student_id>/guardians")
    @login_required
    def add_guardian(student_id):
        school_id = school_context("student.manage_guardians")
        payload = request.get_json(silent=True) or {}
        try:
            name = _clean(payload.get("full_name"),150,True)
            relationship = _clean(payload.get("relationship"),60,True)
            phone = _clean(payload.get("phone"),40)
            email = _clean(payload.get("email"),254)
            if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise ValueError("Enter a valid guardian email address.")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        db = connection_factory()
        try:
            student_or_404(db, school_id, student_id)
            parent_user_id=payload.get("parent_user_id")
            if parent_user_id is not None:
                linked=db.execute("""SELECT 1 FROM users u JOIN school_memberships m ON m.user_id=u.id
                  WHERE u.id=? AND u.role='parent' AND u.status='active'
                    AND m.school_id=? AND m.status='active'""",(parent_user_id,school_id)).fetchone()
                if not linked:
                    return jsonify({"error":"Guardian login must be an active parent membership in this school."}),400
            cur = db.execute("""
              INSERT INTO student_guardians
              (school_id,student_id,full_name,relationship,phone,email,address,occupation,
               parent_user_id,is_primary,is_emergency_contact,consent_status,created_by)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (school_id,student_id,name,relationship,phone,email,
                  _clean(payload.get("address"),300),_clean(payload.get("occupation"),120),
                  parent_user_id,int(bool(payload.get("is_primary"))),
                  int(bool(payload.get("is_emergency_contact"))),
                  payload.get("consent_status","unknown"),int(current_user.id)))
            audit(db,school_id,"student_guardian_added",student_id,"guardian record added")
            db.commit()
            return jsonify({"guardian_id":cur.lastrowid}),201
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    @bp.get("/api/v1/students/<int:student_id>/guardians")
    @login_required
    def list_guardians(student_id):
        school_id = school_context("student.view_sensitive")
        db = connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            rows = db.execute("""
              SELECT id,full_name,relationship,phone,email,address,occupation,is_primary,
                     is_emergency_contact,verified_at,consent_status
              FROM student_guardians WHERE school_id=? AND student_id=? AND archived_at IS NULL
              ORDER BY is_primary DESC,id
            """,(school_id,student_id)).fetchall()
            return jsonify({"items":_json_rows(rows)})
        finally:
            db.close()

    @bp.put("/api/v1/student-guardians/<int:guardian_id>")
    @login_required
    def update_guardian(guardian_id):
        school_id=school_context("student.manage_guardians")
        payload=request.get_json(silent=True) or {}
        try:
            name=_clean(payload.get("full_name"),150,True)
            relationship=_clean(payload.get("relationship"),60,True)
            phone=_clean(payload.get("phone"),40)
            email=_clean(payload.get("email"),254)
            if email and not re.fullmatch(r"[^\\s@]+@[^\\s@]+\\.[^\\s@]+",email):
                raise ValueError("Enter a valid guardian email address.")
        except ValueError as exc: return jsonify({"error":str(exc)}),400
        db=connection_factory()
        try:
            row=db.execute("SELECT student_id FROM student_guardians WHERE id=? AND school_id=? AND archived_at IS NULL",
                           (guardian_id,school_id)).fetchone()
            if not row: abort(404)
            db.execute("""UPDATE student_guardians SET full_name=?,relationship=?,phone=?,email=?,
              address=?,occupation=?,is_primary=?,is_emergency_contact=?,consent_status=?
              WHERE id=? AND school_id=?""",
              (name,relationship,phone,email,_clean(payload.get("address"),300),
               _clean(payload.get("occupation"),120),int(bool(payload.get("is_primary"))),
               int(bool(payload.get("is_emergency_contact"))),payload.get("consent_status","unknown"),
               guardian_id,school_id))
            audit(db,school_id,"student_guardian_updated",row["student_id"],"guardian record updated")
            db.commit(); return jsonify({"guardian_id":guardian_id,"status":"updated"})
        finally: db.close()

    @bp.delete("/api/v1/student-guardians/<int:guardian_id>")
    @login_required
    def archive_guardian(guardian_id):
        school_id=school_context("student.manage_guardians")
        db=connection_factory()
        try:
            row=db.execute("SELECT student_id FROM student_guardians WHERE id=? AND school_id=? AND archived_at IS NULL",
                           (guardian_id,school_id)).fetchone()
            if not row: abort(404)
            db.execute("UPDATE student_guardians SET archived_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?",
                       (guardian_id,school_id))
            audit(db,school_id,"student_guardian_archived",row["student_id"],"guardian relationship archived")
            db.commit(); return jsonify({"guardian_id":guardian_id,"status":"archived"})
        finally: db.close()

    @bp.post("/api/v1/students/<int:student_id>/academic-history")
    @login_required
    def add_academic_history(student_id):
        school_id = school_context("student.manage_records")
        payload = request.get_json(silent=True) or {}
        try:
            institution = _clean(payload.get("institution_name"),150,True)
            start = _valid_date(payload.get("start_date"))
            end = _valid_date(payload.get("end_date"))
            if start and end and end < start: raise ValueError("End date cannot precede start date.")
        except ValueError as exc: return jsonify({"error":str(exc)}),400
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            cur=db.execute("""INSERT INTO student_academic_history
              (school_id,student_id,institution_name,academic_year,class_level,result_summary,
               start_date,end_date,created_by) VALUES(?,?,?,?,?,?,?,?,?)""",
              (school_id,student_id,institution,_clean(payload.get("academic_year"),20),
               _clean(payload.get("class_level"),50),_clean(payload.get("result_summary"),1000),
               start,end,int(current_user.id)))
            audit(db,school_id,"student_academic_history_added",student_id,"history record added")
            db.commit(); return jsonify({"history_id":cur.lastrowid}),201
        finally: db.close()

    @bp.get("/api/v1/students/<int:student_id>/academic-history")
    @login_required
    def list_academic_history(student_id):
        school_id=school_context("student.view_records")
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            rows=db.execute("""SELECT id,institution_name,academic_year,class_level,result_summary,
              start_date,end_date,verified,created_at FROM student_academic_history
              WHERE school_id=? AND student_id=? ORDER BY start_date DESC,id DESC""",(school_id,student_id)).fetchall()
            return jsonify({"items":_json_rows(rows)})
        finally: db.close()

    @bp.post("/api/v1/students/<int:student_id>/transfers")
    @login_required
    def create_transfer(student_id):
        school_id=school_context("student.manage_transfers")
        payload=request.get_json(silent=True) or {}
        transfer_type=payload.get("transfer_type")
        if transfer_type not in {"inbound","outbound","campus"}:
            return jsonify({"error":"Invalid transfer type."}),400
        try: effective=_valid_date(payload.get("effective_date"),True)
        except ValueError as exc: return jsonify({"error":str(exc)}),400
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            if payload.get("destination_school_id") is not None:
                return jsonify({"error":"Cross-tenant transfer handoff requires a platform-authorized workflow."}),400
            cur=db.execute("""INSERT INTO student_transfers
              (school_id,student_id,transfer_type,destination_name,destination_school_id,
               effective_date,reason,created_by) VALUES(?,?,?,?,?,?,?,?)""",
              (school_id,student_id,transfer_type,_clean(payload.get("destination_name"),150),
               payload.get("destination_school_id"),effective,_clean(payload.get("reason"),1000),
               int(current_user.id)))
            audit(db,school_id,"student_transfer_requested",student_id,transfer_type)
            db.commit(); return jsonify({"transfer_id":cur.lastrowid,"status":"requested"}),201
        finally: db.close()

    @bp.post("/api/v1/student-transfers/<int:transfer_id>/review")
    @login_required
    def review_transfer(transfer_id):
        school_id=school_context("student.manage_transfers")
        payload=request.get_json(silent=True) or {}
        state=payload.get("status")
        if state not in {"approved","completed","rejected","cancelled"}:
            return jsonify({"error":"Invalid transfer decision."}),400
        db=connection_factory()
        try:
            transfer=db.execute("SELECT student_id,transfer_type FROM student_transfers WHERE id=? AND school_id=? AND status IN ('requested','approved')",
                                (transfer_id,school_id)).fetchone()
            if not transfer: abort(404)
            db.execute("""UPDATE student_transfers SET status=?,approved_by=?,approved_at=?
              WHERE id=? AND school_id=?""",
              (state,int(current_user.id),datetime.now(timezone.utc).isoformat(),transfer_id,school_id))
            if state=="completed" and transfer["transfer_type"]=="outbound":
                db.execute("UPDATE students SET status='transferred' WHERE id=? AND school_id=?",
                           (transfer["student_id"],school_id))
            audit(db,school_id,"student_transfer_reviewed",transfer["student_id"],state)
            db.commit()
            return jsonify({"transfer_id":transfer_id,"status":state})
        finally: db.close()

    @bp.post("/api/v1/students/<int:student_id>/withdraw")
    @login_required
    def withdraw_student(student_id):
        school_id=school_context("student.manage_transfers")
        payload=request.get_json(silent=True) or {}
        try:
            effective=_valid_date(payload.get("effective_date"),True)
            reason=_clean(payload.get("reason_code"),60,True)
            if reason not in {"family_relocation","graduation","transfer","medical","disciplinary","other"}:
                raise ValueError("Choose a supported withdrawal reason.")
        except ValueError as exc: return jsonify({"error":str(exc)}),400
        db=connection_factory()
        try:
            student=student_or_404(db,school_id,student_id)
            if student["status"] not in {"active","pending"}: return jsonify({"error":"Student is not currently enrolled."}),409
            db.execute("""INSERT INTO student_withdrawals
              (school_id,student_id,effective_date,reason_code,notes,destination_name,approved_by,created_by)
              VALUES(?,?,?,?,?,?,?,?)""",
              (school_id,student_id,effective,reason,_clean(payload.get("notes"),1000),
               _clean(payload.get("destination_name"),150),int(current_user.id),int(current_user.id)))
            db.execute("UPDATE students SET status='withdrawn' WHERE id=? AND school_id=?",
                       (student_id,school_id))
            audit(db,school_id,"student_withdrawn",student_id,reason)
            db.commit(); return jsonify({"student_id":student_id,"status":"withdrawn"}),200
        except Exception:
            db.rollback(); raise
        finally: db.close()

    @bp.post("/api/v1/students/<int:student_id>/disciplinary-records")
    @login_required
    def add_disciplinary_record(student_id):
        school_id=school_context("student.manage_discipline")
        payload=request.get_json(silent=True) or {}
        try:
            incident=_valid_date(payload.get("incident_date"),True)
            category=_clean(payload.get("category"),80,True)
            severity=payload.get("severity")
            description=_clean(payload.get("description"),3000,True)
            if severity not in DISCIPLINE_LEVELS: raise ValueError("Invalid severity.")
        except ValueError as exc: return jsonify({"error":str(exc)}),400
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            cur=db.execute("""INSERT INTO student_disciplinary_records
              (school_id,student_id,incident_date,category,severity,description,action_taken,
               follow_up_date,confidential,recorded_by) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (school_id,student_id,incident,category,severity,description,
               _clean(payload.get("action_taken"),2000),_valid_date(payload.get("follow_up_date")),
               int(bool(payload.get("confidential",True))),int(current_user.id)))
            audit(db,school_id,"student_discipline_recorded",student_id,"restricted record added")
            db.commit(); return jsonify({"disciplinary_record_id":cur.lastrowid}),201
        finally: db.close()

    @bp.get("/api/v1/students/<int:student_id>/disciplinary-records")
    @login_required
    def list_disciplinary_records(student_id):
        school_id=school_context("student.view_discipline")
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            rows=db.execute("""SELECT id,incident_date,category,severity,description,action_taken,
              follow_up_date,confidential,created_at FROM student_disciplinary_records
              WHERE school_id=? AND student_id=? ORDER BY incident_date DESC,id DESC""",
              (school_id,student_id)).fetchall()
            return jsonify({"items":_json_rows(rows)})
        finally: db.close()

    @bp.post("/api/v1/students/<int:student_id>/documents")
    @login_required
    def upload_document(student_id):
        school_id=school_context("student.manage_documents")
        upload=request.files.get("file")
        doc_type=request.form.get("document_type","other")
        if doc_type not in DOCUMENT_TYPES or not upload or not upload.filename:
            return jsonify({"error":"Choose a supported document type and file."}),400
        original=secure_filename(upload.filename)
        suffix=Path(original).suffix.lower()
        if not original or suffix not in ALLOWED_DOCUMENT_EXTENSIONS:
            return jsonify({"error":"Only PDF, PNG and JPEG documents are accepted."}),400
        content=upload.read(5*1024*1024+1)
        if not content or len(content)>5*1024*1024:
            return jsonify({"error":"Document must be between 1 byte and 5 MB."}),400
        # Check basic file signatures; do not trust the browser MIME header.
        signatures={".pdf":content.startswith(b"%PDF-"),
                    ".png":content.startswith(b"\x89PNG\r\n\x1a\n"),
                    ".jpg":content.startswith(b"\xff\xd8\xff"),
                    ".jpeg":content.startswith(b"\xff\xd8\xff")}
        if not signatures[suffix]:
            return jsonify({"error":"File content does not match its extension."}),400
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            stored=uuid.uuid4().hex+suffix
            folder=storage_root/str(school_id)
            folder.mkdir(parents=True,exist_ok=True)
            target=folder/stored
            target.write_bytes(content)
            mime={".pdf":"application/pdf",".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg"}[suffix]
            cur=db.execute("""INSERT INTO student_documents
              (school_id,student_id,document_type,original_name,stored_name,mime_type,file_size,sha256,uploaded_by)
              VALUES(?,?,?,?,?,?,?,?,?)""",
              (school_id,student_id,doc_type,original,stored,mime,len(content),
               hashlib.sha256(content).hexdigest(),int(current_user.id)))
            audit(db,school_id,"student_document_uploaded",student_id,doc_type)
            db.commit(); return jsonify({"document_id":cur.lastrowid,"document_type":doc_type}),201
        except Exception:
            db.rollback()
            if "target" in locals() and target.exists(): target.unlink()
            raise
        finally: db.close()

    @bp.get("/api/v1/students/<int:student_id>/documents")
    @login_required
    def list_documents(student_id):
        school_id=school_context("student.view_sensitive")
        db=connection_factory()
        try:
            student_or_404(db,school_id,student_id)
            rows=db.execute("""SELECT id,document_type,original_name,mime_type,file_size,created_at
              FROM student_documents WHERE school_id=? AND student_id=? AND archived_at IS NULL
              ORDER BY created_at DESC""",(school_id,student_id)).fetchall()
            return jsonify({"items":_json_rows(rows)})
        finally: db.close()

    @bp.get("/api/v1/student-documents/<int:document_id>/download")
    @login_required
    def download_document(document_id):
        school_id=school_context("student.view_sensitive")
        db=connection_factory()
        try:
            row=db.execute("""SELECT stored_name,original_name,mime_type FROM student_documents
              WHERE id=? AND school_id=? AND archived_at IS NULL""",(document_id,school_id)).fetchone()
            if not row: abort(404)
            path=storage_root/str(school_id)/row["stored_name"]
            if not path.is_file(): abort(404)
            return send_file(path,as_attachment=True,download_name=row["original_name"],
                             mimetype=row["mime_type"],conditional=True)
        finally: db.close()

    @bp.delete("/api/v1/student-documents/<int:document_id>")
    @login_required
    def archive_document(document_id):
        school_id=school_context("student.manage_documents")
        db=connection_factory()
        try:
            row=db.execute("SELECT student_id FROM student_documents WHERE id=? AND school_id=? AND archived_at IS NULL",
                           (document_id,school_id)).fetchone()
            if not row: abort(404)
            db.execute("UPDATE student_documents SET archived_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?",
                       (document_id,school_id))
            audit(db,school_id,"student_document_archived",row["student_id"],"document archived")
            db.commit(); return jsonify({"document_id":document_id,"status":"archived"})
        finally: db.close()

    @bp.get("/api/v1/students/export.csv")
    @login_required
    def export_students():
        school_id=school_context("student.export")
        db=connection_factory()
        try:
            include_sensitive=sensitive_allowed(db,school_id)
            fields=["student_id_number","admission_number","full_name","class_level","status","enrollment_date"]
            if include_sensitive: fields += ["date_of_birth","guardian_name","guardian_phone"]
            rows=db.execute("SELECT "+",".join(fields)+" FROM students WHERE school_id=? ORDER BY full_name",
                            (school_id,)).fetchall()
            output=io.StringIO(newline="")
            writer=csv.DictWriter(output,fieldnames=fields); writer.writeheader()
            for row in rows: writer.writerow(dict(row))
            from flask import Response
            response=Response(output.getvalue(),mimetype="text/csv")
            response.headers["Content-Disposition"]="attachment; filename=students.csv"
            audit(db,school_id,"student_exported","school",f"rows={len(rows)}")
            db.commit(); return response
        finally: db.close()

    @bp.post("/api/v1/students/import.csv")
    @login_required
    def import_students():
        school_id=school_context("student.import")
        upload=request.files.get("file")
        if not upload or not upload.filename or Path(upload.filename).suffix.lower()!=".csv":
            return jsonify({"error":"Upload a CSV file."}),400
        raw=upload.read(2*1024*1024+1)
        if len(raw)>2*1024*1024: return jsonify({"error":"CSV must not exceed 2 MB."}),400
        try:
            text=raw.decode("utf-8-sig")
            reader=csv.DictReader(io.StringIO(text))
            required={"admission_number","full_name","class_level"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError("CSV must include admission_number, full_name and class_level.")
            records=list(reader)
            if len(records)>1000: raise ValueError("A single import is limited to 1,000 rows.")
            normalized=[]
            seen=set()
            for row in records:
                admission=_clean(row.get("admission_number"),50,True)
                name=_clean(row.get("full_name"),150,True)
                level=_clean(row.get("class_level"),50,True)
                key=admission.casefold()
                if key in seen: raise ValueError("CSV contains duplicate admission numbers.")
                seen.add(key)
                normalized.append((admission,name,level))
        except (UnicodeDecodeError,ValueError,csv.Error) as exc:
            return jsonify({"error":str(exc)}),400
        db=connection_factory()
        inserted=0
        try:
            db.execute("BEGIN IMMEDIATE")
            for admission,name,level in normalized:
                exists=db.execute("SELECT 1 FROM students WHERE school_id=? AND lower(admission_number)=lower(?)",
                                  (school_id,admission)).fetchone()
                if exists:
                    continue
                identifier=allocate_student_identifier(db,school_id)
                db.execute("""INSERT INTO students(admission_number,student_id_number,full_name,class_level,school_id)
                  VALUES(?,?,?,?,?)""",(admission,identifier,name,level,school_id))
                inserted+=1
            audit(db,school_id,"student_import_completed","school",f"inserted={inserted}; skipped={len(normalized)-inserted}")
            db.commit()
            return jsonify({"inserted":inserted,"skipped":len(normalized)-inserted,"received":len(normalized)}),201
        except Exception:
            db.rollback(); raise
        finally: db.close()

    @bp.get("/api/v1/students/report/summary")
    @login_required
    def student_summary():
        school_id=school_context("student.view_records")
        db=connection_factory()
        try:
            rows=db.execute("""SELECT status,COUNT(*) AS total FROM students
              WHERE school_id=? GROUP BY status ORDER BY status""",(school_id,)).fetchall()
            classes=db.execute("""SELECT class_level,COUNT(*) AS total FROM students
              WHERE school_id=? GROUP BY class_level ORDER BY class_level""",(school_id,)).fetchall()
            admissions=db.execute("""SELECT status,COUNT(*) AS total FROM student_admissions
              WHERE school_id=? GROUP BY status ORDER BY status""",(school_id,)).fetchall()
            return jsonify({"school_id":school_id,"students_by_status":_json_rows(rows),
                            "students_by_class":_json_rows(classes),"admissions_by_status":_json_rows(admissions)})
        finally: db.close()

    return bp
