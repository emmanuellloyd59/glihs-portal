"""Tenant-aware attendance, school calendar and scheduling engine.

This module is additive to the existing Flask/SQLite application. It uses the
existing school context and named authorization permissions; it never accepts
a school ID from an untrusted request as the tenant boundary.
"""
import json
import re
import sqlite3
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STUDENT_STATUSES = {"present", "absent", "late", "excused"}
STAFF_STATUSES = STUDENT_STATUSES | {"remote", "on_leave"}
DAY_TYPES = {"instruction", "holiday", "closure", "event", "weekend"}
MANAGE_ATTENDANCE_ROLES = {
    "super_admin", "school_admin", "school_owner", "principal",
    "vice_principal", "registrar", "academic_admin", "attendance_officer",
}
SCHEDULE_ROLES = {
    "super_admin", "school_admin", "school_owner", "principal",
    "vice_principal", "academic_admin",
}


def _valid_date(value):
    try:
        parsed = date.fromisoformat(str(value))
        return parsed if parsed.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _valid_time(value):
    try:
        parsed = time.fromisoformat(str(value))
        return parsed.strftime("%H:%M") == value
    except (TypeError, ValueError):
        return False


def _school_timezone(db, school_id, campus_id=None):
    # School configuration owns the default timezone. Campus overrides are
    # optional and are read only when that column exists in the current schema.
    columns = {r["name"] for r in db.execute("PRAGMA table_info(schools)")}
    timezone_name = "UTC"
    if "timezone" in columns:
        row = db.execute("SELECT timezone FROM schools WHERE id=?", (school_id,)).fetchone()
        if row and row["timezone"]:
            timezone_name = row["timezone"]
    if campus_id is not None:
        campus_columns = {r["name"] for r in db.execute("PRAGMA table_info(campuses)")}
        if "timezone" in campus_columns:
            row = db.execute(
                "SELECT timezone FROM campuses WHERE id=? AND school_id=?",
                (campus_id, school_id),
            ).fetchone()
            if row and row["timezone"]:
                timezone_name = row["timezone"]
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("The school or campus has an invalid IANA time-zone setting.")


def initialize_attendance_scheduling(dbopen):
    db = dbopen()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS school_calendar_days (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          campus_id INTEGER,
          calendar_date TEXT NOT NULL,
          day_type TEXT NOT NULL CHECK(day_type IN ('instruction','holiday','closure','event','weekend')),
          label TEXT NOT NULL DEFAULT '',
          created_by INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(date(calendar_date) IS NOT NULL)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_school_calendar_day
          ON school_calendar_days(school_id, COALESCE(campus_id,0), calendar_date);
        CREATE TABLE IF NOT EXISTS attendance_sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          campus_id INTEGER,
          academic_year_id INTEGER,
          academic_period_id INTEGER,
          class_level TEXT NOT NULL,
          session_date TEXT NOT NULL,
          session_code TEXT NOT NULL DEFAULT 'daily',
          starts_at TEXT,
          ends_at TEXT,
          status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','submitted','locked')),
          created_by INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(date(session_date) IS NOT NULL),
          CHECK(starts_at IS NULL OR time(starts_at) IS NOT NULL),
          CHECK(ends_at IS NULL OR time(ends_at) IS NOT NULL),
          CHECK(starts_at IS NULL OR ends_at IS NULL OR starts_at < ends_at)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_session
          ON attendance_sessions(school_id, COALESCE(campus_id,0), class_level, session_date, session_code);
        CREATE TABLE IF NOT EXISTS student_attendance_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          campus_id INTEGER,
          session_id INTEGER NOT NULL REFERENCES attendance_sessions(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          status TEXT NOT NULL CHECK(status IN ('present','absent','late','excused')),
          note TEXT,
          recorded_by INTEGER NOT NULL REFERENCES users(id),
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id, session_id, student_id)
        );
        CREATE INDEX IF NOT EXISTS idx_student_attendance_report
          ON student_attendance_records(school_id, campus_id, student_id, recorded_at);
        CREATE TABLE IF NOT EXISTS staff_attendance_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          campus_id INTEGER,
          user_id INTEGER NOT NULL REFERENCES users(id),
          attendance_date TEXT NOT NULL,
          session_code TEXT NOT NULL DEFAULT 'daily',
          status TEXT NOT NULL CHECK(status IN ('present','absent','late','excused','remote','on_leave')),
          note TEXT,
          recorded_by INTEGER NOT NULL REFERENCES users(id),
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(date(attendance_date) IS NOT NULL),
          UNIQUE(school_id, user_id, attendance_date, session_code)
        );
        CREATE TABLE IF NOT EXISTS attendance_correction_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          record_type TEXT NOT NULL CHECK(record_type IN ('student','staff')),
          record_id INTEGER NOT NULL,
          old_status TEXT NOT NULL,
          new_status TEXT NOT NULL,
          reason TEXT NOT NULL,
          requested_by INTEGER NOT NULL REFERENCES users(id),
          decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','approved','rejected')),
          decided_by INTEGER REFERENCES users(id),
          decision_note TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS attendance_correction_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          request_id INTEGER NOT NULL REFERENCES attendance_correction_requests(id),
          actor_user_id INTEGER NOT NULL REFERENCES users(id),
          event_type TEXT NOT NULL CHECK(event_type IN ('requested','approved','rejected')),
          details TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TRIGGER IF NOT EXISTS trg_attendance_correction_event_no_update
          BEFORE UPDATE ON attendance_correction_events
          BEGIN SELECT RAISE(ABORT,'attendance correction history is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS trg_attendance_correction_event_no_delete
          BEFORE DELETE ON attendance_correction_events
          BEGIN SELECT RAISE(ABORT,'attendance correction history is append-only'); END;
        CREATE TABLE IF NOT EXISTS teacher_availability (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          campus_id INTEGER,
          teacher_id INTEGER NOT NULL REFERENCES users(id),
          weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
          starts_at TEXT NOT NULL,
          ends_at TEXT NOT NULL,
          available INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0,1)),
          effective_from TEXT,
          effective_to TEXT,
          created_by INTEGER NOT NULL REFERENCES users(id),
          CHECK(starts_at < ends_at),
          CHECK(effective_from IS NULL OR effective_to IS NULL OR effective_from <= effective_to)
        );
        CREATE INDEX IF NOT EXISTS idx_teacher_availability
          ON teacher_availability(school_id, teacher_id, weekday, starts_at, ends_at);
        CREATE TABLE IF NOT EXISTS schedule_slots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id INTEGER NOT NULL REFERENCES schools(id),
          campus_id INTEGER,
          academic_year_id INTEGER,
          academic_period_id INTEGER,
          class_level TEXT NOT NULL,
          subject TEXT NOT NULL,
          teacher_id INTEGER NOT NULL REFERENCES users(id),
          weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
          starts_at TEXT NOT NULL,
          ends_at TEXT NOT NULL,
          room TEXT NOT NULL DEFAULT '',
          effective_from TEXT,
          effective_to TEXT,
          active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          created_by INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          CHECK(starts_at < ends_at),
          CHECK(effective_from IS NULL OR effective_to IS NULL OR effective_from <= effective_to)
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_slots_tenant
          ON schedule_slots(school_id, campus_id, weekday, starts_at, ends_at, active);
        CREATE TRIGGER IF NOT EXISTS trg_calendar_campus_insert
        BEFORE INSERT ON school_calendar_days
        WHEN NEW.campus_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM campuses c WHERE c.id=NEW.campus_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'calendar campus tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_session_campus_insert
        BEFORE INSERT ON attendance_sessions
        WHEN NEW.campus_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM campuses c WHERE c.id=NEW.campus_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'attendance campus tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_staff_attendance_campus_insert
        BEFORE INSERT ON staff_attendance_records
        WHEN NEW.campus_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM campuses c WHERE c.id=NEW.campus_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'staff attendance campus tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_availability_campus_insert
        BEFORE INSERT ON teacher_availability
        WHEN NEW.campus_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM campuses c WHERE c.id=NEW.campus_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'availability campus tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_schedule_campus_insert
        BEFORE INSERT ON schedule_slots
        WHEN NEW.campus_id IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM campuses c WHERE c.id=NEW.campus_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'schedule campus tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_student_attendance_tenant_insert
        BEFORE INSERT ON student_attendance_records
        WHEN NOT EXISTS (
          SELECT 1 FROM attendance_sessions a JOIN students s
            ON s.id=NEW.student_id AND s.school_id=NEW.school_id
          WHERE a.id=NEW.session_id AND a.school_id=NEW.school_id
            AND COALESCE(a.campus_id,0)=COALESCE(NEW.campus_id,0)
            AND s.class_level=a.class_level
        )
        BEGIN SELECT RAISE(ABORT,'attendance session, student, campus or class tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_attendance_session_academic_insert
        BEFORE INSERT ON attendance_sessions
        WHEN (NEW.academic_year_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM academic_years y WHERE y.id=NEW.academic_year_id AND y.school_id=NEW.school_id))
          OR (NEW.academic_period_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM academic_periods p WHERE p.id=NEW.academic_period_id AND p.school_id=NEW.school_id
                AND (NEW.academic_year_id IS NULL OR p.academic_year_id=NEW.academic_year_id)))
        BEGIN SELECT RAISE(ABORT,'attendance academic period tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_schedule_academic_insert
        BEFORE INSERT ON schedule_slots
        WHEN (NEW.academic_year_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM academic_years y WHERE y.id=NEW.academic_year_id AND y.school_id=NEW.school_id))
          OR (NEW.academic_period_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM academic_periods p WHERE p.id=NEW.academic_period_id AND p.school_id=NEW.school_id
                AND (NEW.academic_year_id IS NULL OR p.academic_year_id=NEW.academic_year_id)))
        BEGIN SELECT RAISE(ABORT,'schedule academic period tenant mismatch'); END;
        """)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def register_attendance_scheduling_routes(ns):
    app = ns["app"]
    login_required = ns["login_required"]
    current_user = ns["current_user"]
    request, jsonify, abort = ns["request"], ns["jsonify"], ns["abort"]
    dbopen, school_context = ns["get_db_connection"], ns["require_school_context"]

    def permission(name, school_id):
        from authorization import require_permission
        require_permission(name, dbopen, school_id=school_id)

    def payload():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="A JSON object is required.")
        return data

    def audit(db, school_id, action, details):
        db.execute(
            "INSERT INTO audit_log(actor_user_id,action,target_username,details,school_id) VALUES(?,?,?,?,?)",
            (int(current_user.id), action, str(current_user.id), details[:500], school_id),
        )

    @app.get("/api/v1/attendance/summary")
    @login_required
    def attendance_scheduling_summary():
        school_id = school_context()
        permission("academic.view_records", school_id)
        start = request.args.get("start_date", "")
        end = request.args.get("end_date", "")
        if not _valid_date(start) or not _valid_date(end) or end < start or (date.fromisoformat(end)-date.fromisoformat(start)).days > 366:
            abort(400, description="Provide a valid date range of no more than 367 days.")
        db = dbopen()
        try:
            class_filter = request.args.get("class_level", "").strip()
            if current_user.role == "teacher":
                if not class_filter or not db.execute(
                    "SELECT 1 FROM teacher_class_assignments WHERE school_id=? AND teacher_id=? AND class_level=? AND active=1",
                    (school_id, int(current_user.id), class_filter),
                ).fetchone():
                    abort(403)
            rows = db.execute("""
              SELECT r.status, COUNT(*) AS total
              FROM student_attendance_records r
              JOIN attendance_sessions s ON s.id=r.session_id AND s.school_id=r.school_id
              WHERE r.school_id=? AND s.session_date BETWEEN ? AND ?
                AND (?='' OR s.class_level=?)
                AND (?<>'teacher' OR EXISTS (
                    SELECT 1 FROM teacher_class_assignments t
                    WHERE t.school_id=s.school_id AND t.teacher_id=? AND t.class_level=s.class_level AND t.active=1
                ))
              GROUP BY r.status
            """, (school_id, start, end, class_filter, class_filter, current_user.role, int(current_user.id))).fetchall()
            staff = []
            if current_user.role in MANAGE_ATTENDANCE_ROLES:
                staff = db.execute("""
                  SELECT status, COUNT(*) AS total FROM staff_attendance_records
                  WHERE school_id=? AND attendance_date BETWEEN ? AND ? GROUP BY status
                """, (school_id, start, end)).fetchall()
            return jsonify({"school_id": school_id, "start_date": start, "end_date": end,
                            "students": {r["status"]: r["total"] for r in rows},
                            "staff": {r["status"]: r["total"] for r in staff}})
        finally:
            db.close()

    @app.post("/api/v1/attendance/sessions")
    @login_required
    def create_attendance_session():
        school_id = school_context()
        permission("academic.manage_attendance", school_id)
        if current_user.role not in MANAGE_ATTENDANCE_ROLES and current_user.role != "teacher":
            abort(403)
        data = payload()
        day = data.get("session_date", "")
        class_level = str(data.get("class_level", "")).strip()
        code = str(data.get("session_code", "daily")).strip()
        campus_id = data.get("campus_id")
        year_id, period_id = data.get("academic_year_id"), data.get("academic_period_id")
        if (not _valid_date(day) or not class_level or len(class_level) > 100
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", code)
                or (data.get("starts_at") is not None and not _valid_time(data.get("starts_at")))
                or (data.get("ends_at") is not None and not _valid_time(data.get("ends_at")))
                or (data.get("starts_at") and data.get("ends_at") and data["starts_at"] >= data["ends_at"])):
            abort(400, description="Invalid session date, class, session code or time range.")
        if campus_id is not None and (not isinstance(campus_id, int) or campus_id < 1):
            abort(400)
        if year_id is not None and (not isinstance(year_id, int) or year_id < 1):
            abort(400)
        if period_id is not None and (not isinstance(period_id, int) or period_id < 1):
            abort(400)
        db = dbopen()
        try:
            tz = _school_timezone(db, school_id, campus_id)
            local_today = datetime.now(tz).date()
            if date.fromisoformat(day) > local_today:
                abort(400, description="Attendance cannot be recorded for a future local school date.")
            if campus_id is not None and not db.execute("SELECT 1 FROM campuses WHERE id=? AND school_id=?", (campus_id, school_id)).fetchone():
                abort(404)
            if current_user.role == "teacher":
                if not db.execute("SELECT 1 FROM teacher_class_assignments WHERE school_id=? AND teacher_id=? AND class_level=? AND active=1", (school_id, int(current_user.id), class_level)).fetchone():
                    abort(403)
            if year_id is not None:
                year = db.execute("SELECT starts_on,ends_on,status FROM academic_years WHERE id=? AND school_id=?", (year_id,school_id)).fetchone()
                if not year or year["status"] not in {"active","draft"} or (year["starts_on"] and day < year["starts_on"]) or (year["ends_on"] and day > year["ends_on"]):
                    abort(400, description="The academic year is not valid for this school date.")
            if period_id is not None:
                period = db.execute("SELECT starts_on,ends_on,status FROM academic_periods WHERE id=? AND school_id=? AND academic_year_id=?", (period_id,school_id,year_id)).fetchone()
                if not period or period["status"] != "active" or (period["starts_on"] and day < period["starts_on"]) or (period["ends_on"] and day > period["ends_on"]):
                    abort(400, description="The academic period is not active for this school date.")
            calendar = db.execute("SELECT day_type FROM school_calendar_days WHERE school_id=? AND COALESCE(campus_id,0) IN (0,COALESCE(?,0)) AND calendar_date=? ORDER BY campus_id DESC LIMIT 1", (school_id,campus_id,day)).fetchone()
            if calendar and calendar["day_type"] in {"holiday","closure","weekend"}:
                abort(400, description="Attendance sessions cannot be opened on a non-instruction day.")
            cur = db.execute("""INSERT INTO attendance_sessions
              (school_id,campus_id,academic_year_id,academic_period_id,class_level,session_date,session_code,starts_at,ends_at,created_by)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (school_id,campus_id,year_id,period_id,class_level,day,code,data.get("starts_at"),data.get("ends_at"),int(current_user.id)))
            audit(db,school_id,"attendance.session_created",f"session_id={cur.lastrowid}; class={class_level}; date={day}")
            db.commit()
            return jsonify({"id": cur.lastrowid, "status": "open"}), 201
        except sqlite3.IntegrityError:
            db.rollback()
            abort(409, description="A matching attendance session already exists or its academic references are invalid.")
        finally:
            db.close()

    @app.post("/api/v1/attendance/sessions/<int:session_id>/entries")
    @login_required
    def record_student_attendance_batch(session_id):
        school_id = school_context()
        permission("academic.manage_attendance", school_id)
        data = payload()
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries or len(entries) > 200:
            abort(400, description="Provide between 1 and 200 attendance entries.")
        db = dbopen()
        try:
            session = db.execute("SELECT * FROM attendance_sessions WHERE id=? AND school_id=?", (session_id,school_id)).fetchone()
            if not session or session["status"] != "open":
                abort(404)
            if current_user.role == "teacher" and not db.execute("SELECT 1 FROM teacher_class_assignments WHERE school_id=? AND teacher_id=? AND class_level=? AND active=1", (school_id,int(current_user.id),session["class_level"])).fetchone():
                abort(403)
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("student_id"), int) or entry.get("status") not in STUDENT_STATUSES:
                    abort(400, description="Each entry needs a student_id and valid status.")
                sid = entry["student_id"]
                if sid in seen:
                    abort(400, description="A student appears more than once in this batch.")
                seen.add(sid)
                student = db.execute("SELECT id FROM students WHERE id=? AND school_id=? AND class_level=? AND status='active'", (sid,school_id,session["class_level"])).fetchone()
                if not student:
                    abort(404, description="A student is not active in this school's selected class.")
            for entry in entries:
                db.execute("""INSERT INTO student_attendance_records
                  (school_id,campus_id,session_id,student_id,status,note,recorded_by)
                  VALUES(?,?,?,?,?,?,?)""",
                  (school_id,session["campus_id"],session_id,entry["student_id"],entry["status"],str(entry.get("note",""))[:250] or None,int(current_user.id)))
            audit(db,school_id,"attendance.batch_recorded",f"session_id={session_id}; entries={len(entries)}")
            db.commit()
            return jsonify({"session_id":session_id,"recorded":len(entries)}), 201
        except sqlite3.IntegrityError:
            db.rollback()
            abort(409, description="Attendance already exists for one or more students in this session.")
        finally:
            db.close()

    @app.post("/api/v1/attendance/staff")
    @login_required
    def record_staff_attendance():
        school_id = school_context()
        permission("academic.manage_attendance", school_id)
        if current_user.role not in MANAGE_ATTENDANCE_ROLES:
            abort(403)
        data = payload()
        user_id, day = data.get("user_id"), data.get("attendance_date", "")
        status, code = data.get("status"), str(data.get("session_code","daily")).strip()
        if not isinstance(user_id,int) or status not in STAFF_STATUSES or not _valid_date(day) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}",code):
            abort(400)
        db = dbopen()
        try:
            tz = _school_timezone(db,school_id,data.get("campus_id"))
            if date.fromisoformat(day) > datetime.now(tz).date():
                abort(400,description="Staff attendance cannot be recorded for a future local school date.")
            member = db.execute("""SELECT 1 FROM school_memberships
                WHERE school_id=? AND user_id=? AND status='active' AND role NOT IN ('student','parent')""",(school_id,user_id)).fetchone()
            if not member:
                abort(404)
            cur = db.execute("""INSERT INTO staff_attendance_records
                (school_id,campus_id,user_id,attendance_date,session_code,status,note,recorded_by)
                VALUES(?,?,?,?,?,?,?,?)""",
                (school_id,data.get("campus_id"),user_id,day,code,status,str(data.get("note",""))[:250] or None,int(current_user.id)))
            audit(db,school_id,"attendance.staff_recorded",f"record_id={cur.lastrowid}; user_id={user_id}; date={day}")
            db.commit()
            return jsonify({"id":cur.lastrowid,"status":status}),201
        except sqlite3.IntegrityError:
            db.rollback()
            abort(409,description="Staff attendance already exists for this date and session.")
        finally:
            db.close()

    @app.post("/api/v1/attendance/corrections")
    @login_required
    def request_attendance_correction():
        school_id = school_context()
        permission("academic.manage_attendance", school_id)
        data = payload()
        record_type, record_id = data.get("record_type"), data.get("record_id")
        reason = str(data.get("reason","")).strip()
        new_status = data.get("new_status")
        allowed_statuses = STUDENT_STATUSES if record_type == "student" else STAFF_STATUSES
        if record_type not in {"student","staff"} or not isinstance(record_id,int) or not reason or len(reason)>500 or new_status not in allowed_statuses:
            abort(400,description="A valid record type, record ID, new status and correction reason are required.")
        db = dbopen()
        try:
            table = "student_attendance_records" if record_type=="student" else "staff_attendance_records"
            row = db.execute(f"SELECT id,status FROM {table} WHERE id=? AND school_id=?",(record_id,school_id)).fetchone()
            if not row:
                abort(404)
            cur = db.execute("""INSERT INTO attendance_correction_requests
              (school_id,record_type,record_id,old_status,new_status,reason,requested_by)
              VALUES(?,?,?,?,?,?,?)""",
              (school_id,record_type,record_id,row["status"],data.get("new_status"),reason,int(current_user.id)))
            db.execute("INSERT INTO attendance_correction_events(school_id,request_id,actor_user_id,event_type,details) VALUES(?,?,?,'requested',?)",(school_id,cur.lastrowid,int(current_user.id),reason))
            db.commit()
            return jsonify({"id":cur.lastrowid,"decision":"pending"}),201
        except sqlite3.IntegrityError:
            db.rollback()
            abort(400,description="Invalid correction status.")
        finally:
            db.close()

    @app.post("/api/v1/attendance/corrections/<int:request_id>/decision")
    @login_required
    def decide_attendance_correction(request_id):
        school_id = school_context()
        permission("academic.manage_attendance", school_id)
        if current_user.role not in MANAGE_ATTENDANCE_ROLES:
            abort(403)
        data = payload()
        decision = data.get("decision")
        if decision not in {"approved","rejected"}:
            abort(400)
        db = dbopen()
        try:
            row = db.execute("SELECT * FROM attendance_correction_requests WHERE id=? AND school_id=? AND decision='pending'",(request_id,school_id)).fetchone()
            if not row:
                abort(404)
            if int(row["requested_by"]) == int(current_user.id):
                abort(403,description="A correction requester cannot approve their own request.")
            if decision == "approved":
                table = "student_attendance_records" if row["record_type"]=="student" else "staff_attendance_records"
                cur = db.execute(f"UPDATE {table} SET status=? WHERE id=? AND school_id=? AND status=?",(row["new_status"],row["record_id"],school_id,row["old_status"]))
                if cur.rowcount != 1:
                    abort(409,description="The attendance record changed after this correction was requested.")
            db.execute("UPDATE attendance_correction_requests SET decision=?,decided_by=?,decision_note=?,decided_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=? AND decision='pending'",(decision,int(current_user.id),str(data.get("note",""))[:500],request_id,school_id))
            db.execute("INSERT INTO attendance_correction_events(school_id,request_id,actor_user_id,event_type,details) VALUES(?,?,?,?,?)",(school_id,request_id,int(current_user.id),decision,str(data.get("note",""))[:500]))
            audit(db,school_id,"attendance.correction_"+decision,f"request_id={request_id}")
            db.commit()
            return jsonify({"id":request_id,"decision":decision})
        except sqlite3.IntegrityError:
            db.rollback()
            abort(400,description="The requested status is not valid for this record type.")
        finally:
            db.close()

    @app.post("/api/v1/school-calendar/days")
    @login_required
    def upsert_school_calendar_day():
        school_id = school_context()
        permission("academic.manage_classes", school_id)
        if current_user.role not in SCHEDULE_ROLES:
            abort(403)
        data = payload()
        day, kind = data.get("calendar_date",""), data.get("day_type")
        campus_id = data.get("campus_id")
        label = str(data.get("label","")).strip()
        if not _valid_date(day) or kind not in DAY_TYPES or len(label)>120:
            abort(400)
        db = dbopen()
        try:
            if campus_id is not None and not db.execute("SELECT 1 FROM campuses WHERE id=? AND school_id=?",(campus_id,school_id)).fetchone():
                abort(404)
            db.execute("""INSERT INTO school_calendar_days(school_id,campus_id,calendar_date,day_type,label,created_by)
                VALUES(?,?,?,?,?,?) ON CONFLICT DO UPDATE SET day_type=excluded.day_type,label=excluded.label""",
                (school_id,campus_id,day,kind,label,int(current_user.id)))
            audit(db,school_id,"calendar.day_saved",f"date={day}; type={kind}; campus={campus_id}")
            db.commit()
            return jsonify({"calendar_date":day,"day_type":kind})
        finally:
            db.close()

    @app.post("/api/v1/scheduling/availability")
    @login_required
    def set_teacher_availability():
        school_id = school_context()
        permission("academic.manage_classes", school_id)
        if current_user.role not in SCHEDULE_ROLES:
            abort(403)
        data = payload()
        teacher_id, weekday = data.get("teacher_id"), data.get("weekday")
        start, end = data.get("starts_at"), data.get("ends_at")
        if (not isinstance(teacher_id,int) or not isinstance(weekday,int) or weekday not in range(7)
                or not _valid_time(start) or not _valid_time(end) or start>=end
                or (data.get("effective_from") and not _valid_date(data["effective_from"]))
                or (data.get("effective_to") and not _valid_date(data["effective_to"]))
                or (data.get("effective_from") and data.get("effective_to") and data["effective_from"] > data["effective_to"])):
            abort(400)
        db = dbopen()
        try:
            if not db.execute("SELECT 1 FROM school_memberships WHERE school_id=? AND user_id=? AND role='teacher' AND status='active'",(school_id,teacher_id)).fetchone():
                abort(404)
            conflict = db.execute("""SELECT 1 FROM teacher_availability WHERE school_id=? AND teacher_id=? AND weekday=?
                AND starts_at<? AND ends_at>? LIMIT 1""",(school_id,teacher_id,weekday,end,start)).fetchone()
            if conflict:
                abort(409,description="Availability windows overlap; replace or edit the existing window.")
            cur = db.execute("""INSERT INTO teacher_availability(school_id,campus_id,teacher_id,weekday,starts_at,ends_at,available,effective_from,effective_to,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (school_id,data.get("campus_id"),teacher_id,weekday,start,end,1 if data.get("available",True) else 0,data.get("effective_from"),data.get("effective_to"),int(current_user.id)))
            audit(db,school_id,"schedule.teacher_availability_saved",f"availability_id={cur.lastrowid}; teacher={teacher_id}")
            db.commit()
            return jsonify({"id":cur.lastrowid}),201
        finally:
            db.close()

    @app.post("/api/v1/scheduling/slots")
    @login_required
    def create_schedule_slot():
        school_id = school_context()
        permission("academic.manage_classes", school_id)
        if current_user.role not in SCHEDULE_ROLES:
            abort(403)
        data = payload()
        class_level, subject = str(data.get("class_level","")).strip(), str(data.get("subject","")).strip()
        teacher_id, weekday = data.get("teacher_id"), data.get("weekday")
        start, end = data.get("starts_at"), data.get("ends_at")
        if (not class_level or len(class_level)>100 or not subject or len(subject)>100
                or not isinstance(teacher_id,int) or not isinstance(weekday,int) or weekday not in range(7)
                or not _valid_time(start) or not _valid_time(end) or start>=end
                or (data.get("effective_from") and not _valid_date(data["effective_from"]))
                or (data.get("effective_to") and not _valid_date(data["effective_to"]))
                or (data.get("effective_from") and data.get("effective_to") and data["effective_from"] > data["effective_to"])):
            abort(400)
        db = dbopen()
        try:
            teacher = db.execute("SELECT 1 FROM school_memberships WHERE school_id=? AND user_id=? AND role='teacher' AND status='active'",(school_id,teacher_id)).fetchone()
            if not teacher:
                abort(404)
            conflicts = db.execute("""SELECT id FROM schedule_slots WHERE school_id=? AND weekday=? AND active=1
                AND starts_at<? AND ends_at>? AND (class_level=? OR teacher_id=? OR (?<>'' AND room=?)) LIMIT 1""",
                (school_id,weekday,end,start,class_level,teacher_id,str(data.get("room","")),str(data.get("room","")))).fetchone()
            if conflicts:
                abort(409,description="The class, teacher or room already has an overlapping timetable slot.")
            available = db.execute("""SELECT 1 FROM teacher_availability WHERE school_id=? AND teacher_id=? AND weekday=?
                AND available=1 AND starts_at<=? AND ends_at>=? LIMIT 1""",(school_id,teacher_id,weekday,start,end)).fetchone()
            blocked = db.execute("""SELECT 1 FROM teacher_availability WHERE school_id=? AND teacher_id=? AND weekday=?
                AND available=0 AND starts_at<? AND ends_at>? LIMIT 1""",(school_id,teacher_id,weekday,end,start)).fetchone()
            if blocked or (db.execute("SELECT 1 FROM teacher_availability WHERE school_id=? AND teacher_id=? AND weekday=? LIMIT 1",(school_id,teacher_id,weekday)).fetchone() and not available):
                abort(409,description="The teacher is unavailable during this time.")
            cur = db.execute("""INSERT INTO schedule_slots
              (school_id,campus_id,academic_year_id,academic_period_id,class_level,subject,teacher_id,weekday,starts_at,ends_at,room,effective_from,effective_to,created_by)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (school_id,data.get("campus_id"),data.get("academic_year_id"),data.get("academic_period_id"),class_level,subject,teacher_id,weekday,start,end,str(data.get("room",""))[:80],data.get("effective_from"),data.get("effective_to"),int(current_user.id)))
            audit(db,school_id,"schedule.slot_created",f"slot_id={cur.lastrowid}; class={class_level}; day={weekday}; time={start}-{end}")
            db.commit()
            return jsonify({"id":cur.lastrowid}),201
        except sqlite3.IntegrityError:
            db.rollback()
            abort(400,description="The schedule's academic year or period is invalid for this school.")
        finally:
            db.close()

    initialize_attendance_scheduling(dbopen)
