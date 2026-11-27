"""Unified, tenant-aware School ERP dashboard, entitlements, and reporting.

This module composes existing school workflows. It intentionally does not create
parallel student, staff, attendance, academic, grading, or finance records.
"""
import csv
import io
import json
from datetime import date
from flask import Blueprint, abort, jsonify, request, send_file, url_for
from flask_login import current_user, login_required

CORE_FEATURES = {
    "admissions": "Admissions",
    "students": "Student information",
    "staff": "Teachers and staff",
    "attendance": "Attendance",
    "academics": "Academic structure",
    "examinations": "Examinations and grading",
    "discipline": "Student discipline",
    "reporting": "Institutional reporting",
}
OPTIONAL_FEATURES = {
    "library": "Library management",
    "transport": "Transport management",
    "inventory": "Inventory management",
}
ALL_FEATURES = {**CORE_FEATURES, **OPTIONAL_FEATURES}
REPORTS = {"students", "admissions", "attendance", "grades", "finance"}


def initialize_school_erp_schema(connection_factory):
    """Create only ERP configuration/audit tables; leave domain records untouched."""
    db = connection_factory()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS school_feature_entitlements (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          feature_key TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
          updated_by INTEGER REFERENCES users(id),
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id,feature_key)
        );
        CREATE TABLE IF NOT EXISTS school_erp_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          actor_user_id INTEGER REFERENCES users(id),
          action TEXT NOT NULL,
          feature_key TEXT,
          details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_erp_entitlements_school
          ON school_feature_entitlements(school_id,enabled,feature_key);
        CREATE INDEX IF NOT EXISTS idx_erp_audit_school_created
          ON school_erp_audit(school_id,created_at);
        """)
        # Core capabilities are always enabled and cannot be disabled by a tenant.
        db.execute("""
          INSERT OR IGNORE INTO school_feature_entitlements(school_id,feature_key,enabled)
          SELECT s.id,f.feature_key,1
          FROM schools s
          CROSS JOIN (
            SELECT 'admissions' feature_key UNION ALL SELECT 'students'
            UNION ALL SELECT 'staff' UNION ALL SELECT 'attendance'
            UNION ALL SELECT 'academics' UNION ALL SELECT 'examinations'
            UNION ALL SELECT 'discipline' UNION ALL SELECT 'reporting'
          ) f
        """)
        # Optional systems start disabled. Enabling an entitlement does not
        # fabricate or migrate library, transport, or inventory records.
        db.execute("""
          INSERT OR IGNORE INTO school_feature_entitlements(school_id,feature_key,enabled)
          SELECT s.id,f.feature_key,0
          FROM schools s
          CROSS JOIN (
            SELECT 'library' feature_key UNION ALL SELECT 'transport'
            UNION ALL SELECT 'inventory'
          ) f
        """)
        db.commit()
    finally:
        db.close()


def _csv_cell(value):
    value = "" if value is None else str(value)
    if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _has_table(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def create_school_erp_blueprint(connection_factory, require_school_context):
    bp = Blueprint("school_erp", __name__, url_prefix="/api/erp")

    def school_id():
        selected = require_school_context()
        if not selected:
            abort(403)
        return int(selected)

    def permission(name, sid):
        from authorization import require_permission
        require_permission(name, connection_factory, school_id=sid)

    def feature_map(db, sid):
        rows = db.execute(
            "SELECT feature_key,enabled FROM school_feature_entitlements WHERE school_id=?",
            (sid,),
        ).fetchall()
        return {row["feature_key"]: bool(row["enabled"]) for row in rows}

    def audit(db, sid, action, feature=None, details=None):
        db.execute(
            """INSERT INTO school_erp_audit
               (school_id,actor_user_id,action,feature_key,details_json)
               VALUES (?,?,?,?,?)""",
            (sid, int(current_user.id), action, feature,
             json.dumps(details or {}, sort_keys=True)),
        )

    @bp.get("/dashboard")
    @login_required
    def dashboard_data():
        sid = school_id()
        permission("school.view_dashboard", sid)
        db = connection_factory()
        try:
            school = db.execute(
                "SELECT id,name FROM schools WHERE id=? AND status='active'", (sid,)
            ).fetchone()
            if school is None:
                abort(403)
            enabled = feature_map(db, sid)
            metrics = {}
            # Each metric has its own permission gate. A role does not gain access
            # to student, academic, or finance data merely by opening the dashboard.
            from authorization import has_permission
            if has_permission(db, "student.view_records", school_id=sid):
                metrics["students"] = {
                    "total": db.execute("SELECT COUNT(*) FROM students WHERE school_id=?", (sid,)).fetchone()[0],
                    "active": db.execute("SELECT COUNT(*) FROM students WHERE school_id=? AND status='active'", (sid,)).fetchone()[0],
                }
            if has_permission(db, "student.manage_admissions", school_id=sid) and _has_table(db, "student_admissions"):
                metrics["admissions"] = {
                    row["status"]: row["count"] for row in db.execute(
                        "SELECT status,COUNT(*) count FROM student_admissions WHERE school_id=? GROUP BY status",
                        (sid,),
                    ).fetchall()
                }
            if has_permission(db, "school.view_staff", school_id=sid) and _has_table(db, "staff_memberships"):
                metrics["staff"] = db.execute(
                    "SELECT COUNT(*) FROM staff_memberships WHERE school_id=? AND membership_status='active'",
                    (sid,),
                ).fetchone()[0]
            if has_permission(db, "academic.view_records", school_id=sid):
                today = date.today().isoformat()
                metrics["attendance_today"] = db.execute(
                    "SELECT COUNT(*) FROM attendance WHERE school_id=? AND attendance_date=?",
                    (sid, today),
                ).fetchone()[0]
                metrics["classes"] = db.execute(
                    "SELECT COUNT(*) FROM grade_levels WHERE school_id=? AND active=1",
                    (sid,),
                ).fetchone()[0] if _has_table(db, "grade_levels") else 0
                metrics["grades_recorded"] = db.execute(
                    "SELECT COUNT(*) FROM grades WHERE school_id=?", (sid,)
                ).fetchone()[0]
            if has_permission(db, "finance.view", school_id=sid):
                metrics["fee_accounts"] = db.execute(
                    "SELECT COUNT(*) FROM student_finances WHERE school_id=?", (sid,)
                ).fetchone()[0]
            return jsonify({
                "school": {"id": school["id"], "name": school["name"]},
                "features": enabled,
                "core_features": list(CORE_FEATURES),
                "optional_features": list(OPTIONAL_FEATURES),
                "metrics": metrics,
                "workflows": {
                    "student_records": "/students",
                    "staff": "/staff",
                    "attendance": "/attendance-report",
                    "academics": "/academics",
                    "assignments": "/assignments",
                    "report_cards": "/report-cards",
                    "institutional_reporting": "/school-performance",
                },
            })
        finally:
            db.close()

    @bp.get("/features")
    @login_required
    def get_features():
        sid = school_id()
        permission("school.view_dashboard", sid)
        db = connection_factory()
        try:
            enabled = feature_map(db, sid)
            return jsonify({
                "core": [{"key": k, "name": v, "enabled": True} for k, v in CORE_FEATURES.items()],
                "optional": [{"key": k, "name": v, "enabled": enabled.get(k, False)}
                             for k, v in OPTIONAL_FEATURES.items()],
            })
        finally:
            db.close()

    @bp.put("/features/<feature_key>")
    @login_required
    def set_feature(feature_key):
        sid = school_id()
        if feature_key not in OPTIONAL_FEATURES:
            abort(400, description="Only optional modules have configurable entitlements.")
        if current_user.role != "super_admin" or not getattr(current_user, "is_super_admin", False):
            abort(403)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            abort(400, description="Provide enabled as a boolean.")
        db = connection_factory()
        try:
            db.execute("""
              INSERT INTO school_feature_entitlements
                (school_id,feature_key,enabled,updated_by,updated_at)
              VALUES (?,?,?,?,CURRENT_TIMESTAMP)
              ON CONFLICT(school_id,feature_key) DO UPDATE SET
                enabled=excluded.enabled,updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
            """, (sid, feature_key, int(payload["enabled"]), int(current_user.id)))
            audit(db, sid, "feature_entitlement_changed", feature_key,
                  {"enabled": payload["enabled"]})
            db.commit()
            return jsonify({"feature": feature_key, "enabled": payload["enabled"]})
        finally:
            db.close()

    @bp.get("/reports/<dataset>")
    @login_required
    def report(dataset):
        sid = school_id()
        if dataset not in REPORTS:
            abort(404)
        required = {
            "students": "student.view_records",
            "admissions": "student.manage_admissions",
            "attendance": "academic.view_records",
            "grades": "academic.view_records",
            "finance": "finance.view",
        }[dataset]
        permission(required, sid)
        year = request.args.get("academic_year", "").strip()[:20]
        term = request.args.get("term", "").strip()[:40]
        class_level = request.args.get("class_level", "").strip()[:80]
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        for candidate in (date_from, date_to):
            if candidate:
                try:
                    if date.fromisoformat(candidate).isoformat() != candidate:
                        raise ValueError
                except ValueError:
                    abort(400, description="Dates must use YYYY-MM-DD.")
        if date_from and date_to and date_from > date_to:
            abort(400, description="date_from must not be after date_to.")
        db = connection_factory()
        try:
            params = [sid]
            if dataset == "students":
                sql = """SELECT admission_number,student_id_number,full_name,class_level,status,
                                enrollment_date,created_at
                         FROM students WHERE school_id=?"""
                if class_level:
                    sql += " AND class_level=?"; params.append(class_level)
                sql += " ORDER BY class_level,full_name LIMIT 5000"
            elif dataset == "admissions":
                sql = """SELECT application_number,applicant_name,requested_class,status,
                                submitted_at,created_at
                         FROM student_admissions WHERE school_id=?"""
                if class_level:
                    sql += " AND requested_class=?"; params.append(class_level)
                sql += " ORDER BY created_at DESC LIMIT 5000"
            elif dataset == "attendance":
                sql = """SELECT s.admission_number,s.full_name,s.class_level,
                                a.attendance_date,a.status,a.note
                         FROM attendance a JOIN students s
                           ON s.id=a.student_id AND s.school_id=a.school_id
                         WHERE a.school_id=?"""
                if class_level:
                    sql += " AND s.class_level=?"; params.append(class_level)
                if date_from:
                    sql += " AND a.attendance_date>=?"; params.append(date_from)
                if date_to:
                    sql += " AND a.attendance_date<=?"; params.append(date_to)
                sql += " ORDER BY a.attendance_date DESC,s.full_name LIMIT 5000"
            elif dataset == "grades":
                sql = """SELECT s.admission_number,s.full_name,s.class_level,g.subject,
                                g.assessment,g.score,g.max_score,g.academic_year,g.term,g.created_at
                         FROM grades g JOIN students s
                           ON s.id=g.student_id AND s.school_id=g.school_id
                         WHERE g.school_id=?"""
                if class_level:
                    sql += " AND s.class_level=?"; params.append(class_level)
                if year:
                    sql += " AND g.academic_year=?"; params.append(year)
                if term:
                    sql += " AND g.term=?"; params.append(term)
                sql += " ORDER BY g.academic_year DESC,g.term,s.class_level,s.full_name,g.subject LIMIT 5000"
            else:
                sql = """SELECT s.admission_number,s.full_name,s.class_level,f.academic_year,
                                f.total_fee,f.amount_paid,(f.total_fee-f.amount_paid) balance,
                                f.updated_at
                         FROM student_finances f JOIN students s
                           ON s.id=f.student_id AND s.school_id=f.school_id
                         WHERE f.school_id=?"""
                if class_level:
                    sql += " AND s.class_level=?"; params.append(class_level)
                if year:
                    sql += " AND f.academic_year=?"; params.append(year)
                sql += " ORDER BY f.academic_year DESC,s.class_level,s.full_name LIMIT 5000"
            rows = db.execute(sql, params).fetchall()
            headers = list(rows[0].keys()) if rows else []
            if request.args.get("format", "").lower() == "csv":
                output = io.StringIO()
                writer = csv.writer(output)
                if headers:
                    writer.writerow(headers)
                    for row in rows:
                        writer.writerow([_csv_cell(row[key]) for key in headers])
                else:
                    writer.writerow(["No records"])
                payload = io.BytesIO(output.getvalue().encode("utf-8-sig"))
                return send_file(payload, mimetype="text/csv",
                                 as_attachment=True, download_name=f"{dataset}-report.csv")
            return jsonify({"dataset": dataset, "count": len(rows),
                            "limit": 5000, "filters": {
                                "academic_year": year or None, "term": term or None,
                                "class_level": class_level or None,
                                "date_from": date_from or None, "date_to": date_to or None,
                            }, "columns": headers, "rows": [dict(row) for row in rows]})
        finally:
            db.close()

    return bp
