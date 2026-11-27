"""Tenant-scoped, configurable academic structure engine.

This module is additive: it does not replace legacy student, grade, or timetable
records. Labels are data, not enumerated national-grade or calendar constants.
"""
import json
import re
import sqlite3
from datetime import date


PERIOD_TYPES = {"term", "semester", "quarter", "trimester", "module", "block", "custom"}
MANAGER_ROLES = {"super_admin", "school_admin", "principal", "vice_principal", "academic_admin"}
VIEW_ROLES = MANAGER_ROLES | {"registrar", "teacher"}


def initialize_academic_structure(dbopen):
    """Create versioned tenant-owned structures and safely import legacy labels."""
    db = dbopen()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS academic_years (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          label TEXT NOT NULL, starts_on TEXT, ends_on TEXT,
          status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','closed','archived')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(school_id,label),
          CHECK(starts_on IS NULL OR date(starts_on) IS NOT NULL),
          CHECK(ends_on IS NULL OR date(ends_on) IS NOT NULL),
          CHECK(starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on)
        );
        CREATE TABLE IF NOT EXISTS academic_periods (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
          name TEXT NOT NULL, period_type TEXT NOT NULL, sequence_no INTEGER NOT NULL DEFAULT 1,
          starts_on TEXT, ends_on TEXT, status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active','closed','archived')),
          UNIQUE(school_id,academic_year_id,name), CHECK(sequence_no > 0),
          CHECK(starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on)
        );
        CREATE TABLE IF NOT EXISTS grade_levels (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          name TEXT NOT NULL, code TEXT, sequence_no INTEGER NOT NULL DEFAULT 1,
          progression_group TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          UNIQUE(school_id,name), UNIQUE(school_id,code), CHECK(sequence_no > 0)
        );
        CREATE TABLE IF NOT EXISTS academic_classes (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
          grade_level_id INTEGER NOT NULL REFERENCES grade_levels(id),
          name TEXT NOT NULL, code TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          UNIQUE(school_id,academic_year_id,name), UNIQUE(school_id,academic_year_id,code)
        );
        CREATE TABLE IF NOT EXISTS class_sections (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          class_id INTEGER NOT NULL REFERENCES academic_classes(id), name TEXT NOT NULL,
          code TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          UNIQUE(school_id,class_id,name), UNIQUE(school_id,class_id,code)
        );
        CREATE TABLE IF NOT EXISTS subjects (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          name TEXT NOT NULL, code TEXT, description TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          UNIQUE(school_id,name), UNIQUE(school_id,code)
        );
        CREATE TABLE IF NOT EXISTS curriculum_subjects (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          curriculum_name TEXT NOT NULL, curriculum_version TEXT,
          grade_level_id INTEGER NOT NULL REFERENCES grade_levels(id),
          subject_id INTEGER NOT NULL REFERENCES subjects(id),
          requirement TEXT NOT NULL DEFAULT 'required' CHECK(requirement IN ('required','elective','optional')),
          credits REAL, effective_from TEXT, effective_to TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          UNIQUE(school_id,curriculum_name,curriculum_version,grade_level_id,subject_id),
          CHECK(credits IS NULL OR credits >= 0),
          CHECK(effective_from IS NULL OR effective_to IS NULL OR effective_from <= effective_to)
        );
        CREATE TABLE IF NOT EXISTS course_enrollments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          student_id INTEGER NOT NULL REFERENCES students(id),
          class_section_id INTEGER NOT NULL REFERENCES class_sections(id),
          subject_id INTEGER NOT NULL REFERENCES subjects(id),
          academic_year_id INTEGER NOT NULL REFERENCES academic_years(id),
          academic_period_id INTEGER REFERENCES academic_periods(id),
          status TEXT NOT NULL DEFAULT 'enrolled' CHECK(status IN ('enrolled','completed','withdrawn')),
          enrolled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id,student_id,class_section_id,subject_id,academic_year_id,academic_period_id)
        );
        CREATE TABLE IF NOT EXISTS academic_structure_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          version_no INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
          change_note TEXT, created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(school_id,version_no)
        );
        CREATE INDEX IF NOT EXISTS idx_academic_periods_tenant_year ON academic_periods(school_id,academic_year_id);
        CREATE INDEX IF NOT EXISTS idx_academic_classes_tenant_year ON academic_classes(school_id,academic_year_id);
        CREATE INDEX IF NOT EXISTS idx_course_enrollments_student ON course_enrollments(school_id,student_id,academic_year_id);
        CREATE TRIGGER IF NOT EXISTS trg_period_tenant_insert BEFORE INSERT ON academic_periods
        WHEN NOT EXISTS (SELECT 1 FROM academic_years y WHERE y.id=NEW.academic_year_id AND y.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'academic period tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_period_tenant_update BEFORE UPDATE OF school_id,academic_year_id ON academic_periods
        WHEN NOT EXISTS (SELECT 1 FROM academic_years y WHERE y.id=NEW.academic_year_id AND y.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'academic period tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_class_tenant_insert BEFORE INSERT ON academic_classes
        WHEN NOT EXISTS (SELECT 1 FROM academic_years y JOIN grade_levels g ON g.school_id=y.school_id WHERE y.id=NEW.academic_year_id AND g.id=NEW.grade_level_id AND y.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'academic class tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_class_tenant_update BEFORE UPDATE OF school_id,academic_year_id,grade_level_id ON academic_classes
        WHEN NOT EXISTS (SELECT 1 FROM academic_years y JOIN grade_levels g ON g.school_id=y.school_id WHERE y.id=NEW.academic_year_id AND g.id=NEW.grade_level_id AND y.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'academic class tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_section_tenant_insert BEFORE INSERT ON class_sections
        WHEN NOT EXISTS (SELECT 1 FROM academic_classes c WHERE c.id=NEW.class_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'class section tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_section_tenant_update BEFORE UPDATE OF school_id,class_id ON class_sections
        WHEN NOT EXISTS (SELECT 1 FROM academic_classes c WHERE c.id=NEW.class_id AND c.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'class section tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_curriculum_tenant_insert BEFORE INSERT ON curriculum_subjects
        WHEN NOT EXISTS (SELECT 1 FROM grade_levels g JOIN subjects s ON s.school_id=g.school_id WHERE g.id=NEW.grade_level_id AND s.id=NEW.subject_id AND g.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'curriculum tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_curriculum_tenant_update BEFORE UPDATE OF school_id,grade_level_id,subject_id ON curriculum_subjects
        WHEN NOT EXISTS (SELECT 1 FROM grade_levels g JOIN subjects s ON s.school_id=g.school_id WHERE g.id=NEW.grade_level_id AND s.id=NEW.subject_id AND g.school_id=NEW.school_id)
        BEGIN SELECT RAISE(ABORT,'curriculum tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_enrollment_tenant_insert BEFORE INSERT ON course_enrollments
        WHEN NOT EXISTS (
          SELECT 1 FROM students st
          JOIN class_sections cs ON cs.id=NEW.class_section_id AND cs.school_id=st.school_id
          JOIN academic_classes ac ON ac.id=cs.class_id AND ac.school_id=cs.school_id AND ac.academic_year_id=NEW.academic_year_id
          JOIN subjects su ON su.id=NEW.subject_id AND su.school_id=st.school_id
          JOIN academic_years ay ON ay.id=NEW.academic_year_id AND ay.school_id=st.school_id
          WHERE st.id=NEW.student_id AND st.school_id=NEW.school_id
            AND (NEW.academic_period_id IS NULL OR EXISTS
              (SELECT 1 FROM academic_periods p WHERE p.id=NEW.academic_period_id AND p.school_id=NEW.school_id AND p.academic_year_id=NEW.academic_year_id))
        )
        BEGIN SELECT RAISE(ABORT,'course enrollment tenant or year mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_enrollment_tenant_update BEFORE UPDATE OF school_id,student_id,class_section_id,subject_id,academic_year_id,academic_period_id ON course_enrollments
        WHEN NOT EXISTS (
          SELECT 1 FROM students st
          JOIN class_sections cs ON cs.id=NEW.class_section_id AND cs.school_id=st.school_id
          JOIN academic_classes ac ON ac.id=cs.class_id AND ac.school_id=cs.school_id AND ac.academic_year_id=NEW.academic_year_id
          JOIN subjects su ON su.id=NEW.subject_id AND su.school_id=st.school_id
          JOIN academic_years ay ON ay.id=NEW.academic_year_id AND ay.school_id=st.school_id
          WHERE st.id=NEW.student_id AND st.school_id=NEW.school_id
            AND (NEW.academic_period_id IS NULL OR EXISTS
              (SELECT 1 FROM academic_periods p WHERE p.id=NEW.academic_period_id AND p.school_id=NEW.school_id AND p.academic_year_id=NEW.academic_year_id))
        )
        BEGIN SELECT RAISE(ABORT,'course enrollment tenant or year mismatch'); END;
        """)
        # Add nullable references to existing grade/report rows. Existing textual
        # columns remain authoritative for old reports and are never rewritten.
        for table in ("grades", "report_cards"):
            cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            if not cols:
                continue
            if "academic_year_id" not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN academic_year_id INTEGER REFERENCES academic_years(id)")
            if "academic_period_id" not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN academic_period_id INTEGER REFERENCES academic_periods(id)")
            if table == "grades" and "subject_id" not in cols:
                db.execute("ALTER TABLE grades ADD COLUMN subject_id INTEGER REFERENCES subjects(id)")
        # Backfill only recognizable legacy labels; never guess dates or curriculum.
        schools = db.execute("SELECT id FROM schools").fetchall()
        for school in schools:
            sid = school["id"]
            year_labels = set()
            for table in ("grades", "report_cards"):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if "academic_year" in cols and "school_id" in cols:
                    year_labels.update(r[0] for r in db.execute(
                        f"SELECT DISTINCT academic_year FROM {table} WHERE school_id=? AND academic_year IS NOT NULL AND trim(academic_year)<>'' AND academic_year<>'Unassigned'", (sid,)
                    ))
            for label in sorted(year_labels):
                db.execute("INSERT OR IGNORE INTO academic_years(school_id,label,status) VALUES(?,?, 'closed')", (sid,label))
            period_labels = set()
            for table in ("grades", "report_cards"):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if {"term","academic_year","school_id"}.issubset(cols):
                    period_labels.update((r["academic_year"],r["term"]) for r in db.execute(
                        f"SELECT DISTINCT academic_year,term FROM {table} WHERE school_id=? AND term IS NOT NULL AND trim(term)<>'' AND term<>'Unassigned' AND academic_year<>'Unassigned'", (sid,)
                    ))
            for year_label, term_label in period_labels:
                year = db.execute("SELECT id FROM academic_years WHERE school_id=? AND label=?", (sid,year_label)).fetchone()
                if year:
                    db.execute("INSERT OR IGNORE INTO academic_periods(school_id,academic_year_id,name,period_type,status) VALUES(?,?,?,'custom','closed')", (sid,year["id"],term_label))
            # Link legacy grade/report rows to the imported year and period IDs
            # without changing their original labels or scores.
            for table in ("grades", "report_cards"):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if {"school_id","academic_year","term","academic_year_id","academic_period_id"}.issubset(cols):
                    db.execute(f"""UPDATE {table}
                        SET academic_year_id=(SELECT y.id FROM academic_years y
                            WHERE y.school_id={table}.school_id AND y.label={table}.academic_year),
                            academic_period_id=(SELECT p.id FROM academic_periods p
                                JOIN academic_years y ON y.id=p.academic_year_id AND y.school_id=p.school_id
                                WHERE p.school_id={table}.school_id AND y.label={table}.academic_year
                                  AND p.name={table}.term)
                        WHERE school_id=? AND academic_year<>'Unassigned' AND term<>'Unassigned'""", (sid,))
            # Legacy classes and subjects are imported as labels, not assigned to a
            # national progression or assumed curriculum. Legacy class_level is
            # intentionally not treated as a class/section because old records do
            # not distinguish a grade level from a homeroom or section.
            legacy_classes = set()
            for table, column in (("students","class_level"),("teacher_class_assignments","class_level"),("class_timetable","class_level")):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column in cols and "school_id" in cols:
                    legacy_classes.update(r[0] for r in db.execute(f"SELECT DISTINCT {column} FROM {table} WHERE school_id=? AND {column} IS NOT NULL AND trim({column})<>''", (sid,)))
            for index, label in enumerate(sorted(legacy_classes),1):
                db.execute("INSERT OR IGNORE INTO grade_levels(school_id,name,sequence_no) VALUES(?,?,?)",(sid,label,index))
            legacy_subjects = set()
            for table, column in (("grades","subject"),("teacher_class_assignments","subject"),("class_timetable","subject")):
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column in cols and "school_id" in cols:
                    legacy_subjects.update(r[0] for r in db.execute(f"SELECT DISTINCT {column} FROM {table} WHERE school_id=? AND {column} IS NOT NULL AND trim({column})<>''",(sid,)))
            for label in sorted(legacy_subjects):
                db.execute("INSERT OR IGNORE INTO subjects(school_id,name) VALUES(?,?)",(sid,label))
            # Link historical grade rows to the tenant's canonical subject while
            # preserving the original free-text subject column for old interfaces.
            grade_cols = {r["name"] for r in db.execute("PRAGMA table_info(grades)")}
            if {"school_id","subject","subject_id"}.issubset(grade_cols):
                db.execute("""UPDATE grades SET subject_id=(SELECT s.id FROM subjects s
                    WHERE s.school_id=grades.school_id AND lower(s.name)=lower(grades.subject))
                    WHERE school_id=? AND subject_id IS NULL""",(sid,))
            if db.execute("SELECT COUNT(*) FROM academic_structure_versions WHERE school_id=?",(sid,)).fetchone()[0] == 0:
                _snapshot(db,sid,None,"Initial import of existing academic labels")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _snapshot(db, school_id, actor_id, note):
    tables = ("academic_years","academic_periods","grade_levels","academic_classes","class_sections","subjects","curriculum_subjects")
    data = {}
    for table in tables:
        data[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE school_id=? ORDER BY id",(school_id,))]
    latest = db.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM academic_structure_versions WHERE school_id=?",(school_id,)).fetchone()[0]
    db.execute("INSERT INTO academic_structure_versions(school_id,version_no,snapshot_json,change_note,created_by) VALUES(?,?,?,?,?)",
               (school_id,latest,json.dumps(data,sort_keys=True),note,actor_id))


def register_academic_structure_routes(ns):
    app=ns["app"]; login_required=ns["login_required"]; current_user=ns["current_user"]
    request=ns["request"]; render_template=ns["render_template"]; redirect=ns["redirect"]
    url_for=ns["url_for"]; flash=ns["flash"]; abort=ns["abort"]
    dbopen=ns["get_db_connection"]; school_context=ns["require_school_context"]
    roles=MANAGER_ROLES
    initialize_academic_structure(dbopen)

    @app.route("/academic-structure",methods=["GET","POST"])
    @login_required
    def academic_structure():
        if current_user.role not in VIEW_ROLES:
            abort(403)
        school_id=school_context()
        can_manage=current_user.role in roles
        db=dbopen()
        try:
            if request.method=="POST":
                if not can_manage:
                    abort(403)
                action=request.form.get("action","")
                name=request.form.get("name","").strip()
                try:
                    if action=="year":
                        start=request.form.get("starts_on") or None; end=request.form.get("ends_on") or None
                        if not name or len(name)>80 or (start and end and start>end): raise ValueError("Enter a valid academic-year label and date range.")
                        db.execute("INSERT INTO academic_years(school_id,label,starts_on,ends_on) VALUES(?,?,?,?)",(school_id,name,start,end))
                    elif action=="period":
                        year_id=request.form.get("academic_year_id","")
                        kind=request.form.get("period_type","")
                        seq=request.form.get("sequence_no","1")
                        if not year_id.isdigit() or kind not in PERIOD_TYPES or not seq.isdigit() or int(seq)<1 or not name: raise ValueError("Choose a valid year, period type, name and positive sequence.")
                        db.execute("INSERT INTO academic_periods(school_id,academic_year_id,name,period_type,sequence_no,starts_on,ends_on) SELECT ?,id,?,?,?,?,? FROM academic_years WHERE id=? AND school_id=?",(school_id,name,kind,int(seq),request.form.get("starts_on") or None,request.form.get("ends_on") or None,int(year_id),school_id))
                        if db.execute("SELECT changes()").fetchone()[0]!=1: raise ValueError("The selected academic year does not belong to this school.")
                    elif action=="grade_level":
                        seq=request.form.get("sequence_no","1")
                        if not name or len(name)>80 or not seq.isdigit() or int(seq)<1: raise ValueError("Enter a valid grade/stage name and sequence.")
                        db.execute("INSERT INTO grade_levels(school_id,name,code,sequence_no,progression_group) VALUES(?,?,?,?,?)",(school_id,name,request.form.get("code") or None,int(seq),request.form.get("progression_group") or None))
                    elif action=="subject":
                        if not name or len(name)>100: raise ValueError("Enter a valid subject name.")
                        db.execute("INSERT INTO subjects(school_id,name,code,description) VALUES(?,?,?,?)",(school_id,name,request.form.get("code") or None,request.form.get("description") or None))
                    elif action=="class":
                        year_id=request.form.get("academic_year_id",""); level_id=request.form.get("grade_level_id","")
                        if not year_id.isdigit() or not level_id.isdigit() or not name: raise ValueError("Choose a valid year and grade/stage, and enter a class name.")
                        db.execute("INSERT INTO academic_classes(school_id,academic_year_id,grade_level_id,name,code) SELECT ?,y.id,g.id,?,? FROM academic_years y JOIN grade_levels g ON g.id=? AND g.school_id=y.school_id WHERE y.id=? AND y.school_id=?",(school_id,name,request.form.get("code") or None,int(level_id),int(year_id),school_id))
                        if db.execute("SELECT changes()").fetchone()[0]!=1: raise ValueError("The selected year and progression level must belong to this school.")
                    elif action=="section":
                        class_id=request.form.get("class_id","")
                        if not class_id.isdigit() or not name: raise ValueError("Choose a valid class and enter a section name.")
                        db.execute("INSERT INTO class_sections(school_id,class_id,name,code) SELECT ?,id,?,? FROM academic_classes WHERE id=? AND school_id=?",(school_id,name,request.form.get("code") or None,int(class_id),school_id))
                        if db.execute("SELECT changes()").fetchone()[0]!=1: raise ValueError("The selected class does not belong to this school.")
                    elif action=="curriculum_subject":
                        level_id=request.form.get("grade_level_id",""); subject_id=request.form.get("subject_id","")
                        curriculum=request.form.get("curriculum_name","").strip()
                        requirement=request.form.get("requirement","required")
                        if not level_id.isdigit() or not subject_id.isdigit() or not curriculum or requirement not in {"required","elective","optional"}: raise ValueError("Choose a valid grade/stage and subject and curriculum requirement.")
                        db.execute("INSERT INTO curriculum_subjects(school_id,curriculum_name,curriculum_version,grade_level_id,subject_id,requirement,credits,effective_from,effective_to) SELECT ?,?,?,g.id,s.id,?,?,?,? FROM grade_levels g JOIN subjects s ON s.id=? AND s.school_id=g.school_id WHERE g.id=? AND g.school_id=?",(school_id,curriculum,request.form.get("curriculum_version") or None,requirement,float(request.form["credits"]) if request.form.get("credits") else None,request.form.get("effective_from") or None,request.form.get("effective_to") or None,int(subject_id),int(level_id),school_id))
                        if db.execute("SELECT changes()").fetchone()[0]!=1: raise ValueError("The selected progression level and subject must belong to this school.")
                    elif action=="enroll":
                        values=[request.form.get(k,"") for k in ("student_id","class_section_id","subject_id","academic_year_id")]
                        if not all(v.isdigit() for v in values): raise ValueError("Choose a valid student, section, subject and academic year.")
                        period=request.form.get("academic_period_id") or None
                        if period and not period.isdigit():
                            raise ValueError("Choose a valid academic period.")
                        base_sql = """INSERT INTO course_enrollments
                            (school_id,student_id,class_section_id,subject_id,academic_year_id,academic_period_id)
                            SELECT ?,s.id,cs.id,su.id,ay.id,? FROM students s
                            JOIN class_sections cs ON cs.id=? AND cs.school_id=?
                            JOIN academic_classes ac ON ac.id=cs.class_id AND ac.school_id=cs.school_id
                            JOIN subjects su ON su.id=? AND su.school_id=?
                            JOIN academic_years ay ON ay.id=? AND ay.school_id=?
                            WHERE s.id=? AND s.school_id=? AND s.status='active' AND cs.active=1 AND su.active=1 AND ay.status IN ('draft','active') AND ac.academic_year_id=ay.id"""
                        params=[school_id,int(period) if period else None,int(values[1]),school_id,int(values[2]),school_id,int(values[3]),school_id,int(values[0]),school_id]
                        if period:
                            base_sql += " AND EXISTS (SELECT 1 FROM academic_periods p WHERE p.id=? AND p.school_id=? AND p.academic_year_id=ay.id)"
                            params.extend([int(period),school_id])
                        db.execute(base_sql,params)
                        if db.execute("SELECT changes()").fetchone()[0]!=1: raise ValueError("The selected enrollment records must all belong to this school and academic year.")
                    else:
                        raise ValueError("Unknown academic-structure action.")
                    if action != "enroll":
                        _snapshot(db,school_id,int(current_user.id),action)
                    db.commit(); flash("Academic structure saved.", "success")
                except (ValueError,sqlite3.IntegrityError) as exc:
                    db.rollback(); flash(str(exc) or "The record conflicts with an existing academic structure.", "danger")
                return redirect(url_for("academic_structure"))
            def rows(table, order="id"):
                return db.execute(f"SELECT * FROM {table} WHERE school_id=? ORDER BY {order}",(school_id,)).fetchall()
            years=rows("academic_years","id DESC")
            periods=db.execute("SELECT p.*,y.label AS year_label FROM academic_periods p JOIN academic_years y ON y.id=p.academic_year_id AND y.school_id=p.school_id WHERE p.school_id=? ORDER BY y.id DESC,p.sequence_no",(school_id,)).fetchall()
            levels=rows("grade_levels","sequence_no,name")
            subjects=rows("subjects","name")
            classes=db.execute("SELECT c.*,g.name AS grade_name,y.label AS year_label FROM academic_classes c JOIN grade_levels g ON g.id=c.grade_level_id AND g.school_id=c.school_id JOIN academic_years y ON y.id=c.academic_year_id AND y.school_id=c.school_id WHERE c.school_id=? ORDER BY c.id DESC",(school_id,)).fetchall()
            sections=db.execute("SELECT s.*,c.name AS class_name FROM class_sections s JOIN academic_classes c ON c.id=s.class_id AND c.school_id=s.school_id WHERE s.school_id=? ORDER BY s.id DESC",(school_id,)).fetchall()
            curricula=db.execute("SELECT cs.*,g.name AS grade_name,s.name AS subject_name FROM curriculum_subjects cs JOIN grade_levels g ON g.id=cs.grade_level_id AND g.school_id=cs.school_id JOIN subjects s ON s.id=cs.subject_id AND s.school_id=cs.school_id WHERE cs.school_id=? ORDER BY cs.curriculum_name,g.sequence_no,s.name",(school_id,)).fetchall()
            versions=rows("academic_structure_versions","version_no DESC")
            students=db.execute("SELECT id,full_name,admission_number FROM students WHERE school_id=? AND status='active' ORDER BY full_name",(school_id,)).fetchall()
            enrollments=db.execute("""SELECT e.id,st.full_name,st.admission_number,cs.name AS section_name,
                    c.name AS class_name,su.name AS subject_name,y.label AS year_label,
                    p.name AS period_name,e.status,e.enrolled_at
                FROM course_enrollments e
                JOIN students st ON st.id=e.student_id AND st.school_id=e.school_id
                JOIN class_sections sec ON sec.id=e.class_section_id AND sec.school_id=e.school_id
                JOIN academic_classes c ON c.id=sec.class_id AND c.school_id=e.school_id
                JOIN subjects su ON su.id=e.subject_id AND su.school_id=e.school_id
                JOIN academic_years y ON y.id=e.academic_year_id AND y.school_id=e.school_id
                LEFT JOIN academic_periods p ON p.id=e.academic_period_id AND p.school_id=e.school_id
                WHERE e.school_id=? ORDER BY e.id DESC LIMIT 100""",(school_id,)).fetchall()
        finally:
            db.close()
        return render_template("academic_structure.html",can_manage=can_manage,years=years,periods=periods,levels=levels,subjects=subjects,classes=classes,sections=sections,curricula=curricula,versions=versions,students=students,enrollments=enrollments,period_types=sorted(PERIOD_TYPES))
