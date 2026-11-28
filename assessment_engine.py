"""Tenant-aware configurable assessment and grading engine."""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

ROLES_CONFIGURE={"principal","super_admin","school_admin","academic_admin"}
ROLES_GRADE=ROLES_CONFIGURE|{"teacher","registrar"}
ROLES_APPROVE=ROLES_CONFIGURE
ASSESSMENT_TYPES={"examination","continuous_assessment","assignment","project","practical","oral"}

def _decimal(value,label):
    try: n=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError): raise ValueError(f"{label} must be a valid number")
    if not n.is_finite(): raise ValueError(f"{label} must be finite")
    return n

def validate_grade_bands(bands):
    if not isinstance(bands,list) or not bands: raise ValueError("At least one grade boundary is required")
    out=[]
    for band in bands:
        lo,hi=_decimal(band.get("minimum"),"minimum"),_decimal(band.get("maximum"),"maximum")
        grade=str(band.get("grade","")).strip()
        if lo<0 or hi>100 or lo>=hi or not grade or len(grade)>24:
            raise ValueError("Grade boundaries must be valid ranges within 0–100")
        out.append({"minimum":str(lo),"maximum":str(hi),"grade":grade,"remark":str(band.get("remark",""))[:160]})
    out.sort(key=lambda x:Decimal(x["minimum"]))
    if Decimal(out[0]["minimum"])!=0 or Decimal(out[-1]["maximum"])!=100:
        raise ValueError("Grade boundaries must cover 0–100")
    for a,b in zip(out,out[1:]):
        if Decimal(a["maximum"])!=Decimal(b["minimum"]): raise ValueError("Grade boundaries must be contiguous without gaps or overlaps")
    return out

def calculate_weighted_result(components,bands):
    if not components: raise ValueError("At least one scored assessment is required")
    total_weight=Decimal(0); weighted=Decimal(0); details=[]
    for c in components:
        w,s,m=(_decimal(c[k],k) for k in ("weight","score","max_score"))
        if w<=0 or m<=0 or s<0 or s>m: raise ValueError("Weights and maximum scores must be positive; scores must be within range")
        total_weight+=w; weighted+=(s/m)*w
        details.append({"assessment_id":int(c["assessment_id"]),"score":str(s),"max_score":str(m),"weight":str(w)})
    pct=(weighted/total_weight*100).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    normalized=validate_grade_bands(bands)
    band=next((b for b in normalized if Decimal(b["minimum"])<=pct and (pct<Decimal(b["maximum"]) or (pct==100 and Decimal(b["maximum"])==100))),None)
    if band is None: raise ValueError("No grade boundary matches result")
    return {"percentage":str(pct),"grade":band["grade"],"remark":band["remark"],"components":details,"weight_total":str(total_weight)}

def grade_band_for_percentage(percentage, bands):
    """Return the frozen grading band matching a percentage, including 100."""
    pct = _decimal(percentage, "percentage")
    if pct < 0 or pct > 100:
        raise ValueError("Percentage must be within 0–100")
    normalized = validate_grade_bands(bands)
    return next((band for band in normalized
                 if Decimal(band["minimum"]) <= pct and
                 (pct < Decimal(band["maximum"]) or
                  (pct == 100 and Decimal(band["maximum"]) == 100))), None)


def initialize_assessment_schema(get_db_connection):
    db=get_db_connection()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS assessment_grading_scales(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,name TEXT NOT NULL,
          version INTEGER NOT NULL,bands_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',
          created_by INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id,name,version),UNIQUE(id,school_id));
        CREATE TABLE IF NOT EXISTS configurable_assessments(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,campus_id INTEGER,
          academic_period_id INTEGER,title TEXT NOT NULL,assessment_type TEXT NOT NULL,
          subject TEXT NOT NULL,class_level TEXT NOT NULL,academic_year TEXT NOT NULL,
          period_name TEXT NOT NULL,weight TEXT NOT NULL,max_score TEXT NOT NULL,due_date TEXT,
          status TEXT NOT NULL DEFAULT 'draft',created_by INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(id,school_id));
        CREATE TABLE IF NOT EXISTS assessment_scores(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,assessment_id INTEGER NOT NULL,
          student_id INTEGER NOT NULL,score TEXT NOT NULL,teacher_comment TEXT NOT NULL DEFAULT '',
          recorded_by INTEGER NOT NULL,recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id,assessment_id,student_id),
          FOREIGN KEY(assessment_id,school_id) REFERENCES configurable_assessments(id,school_id));
        CREATE TABLE IF NOT EXISTS assessment_results(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,student_id INTEGER NOT NULL,
          academic_year TEXT NOT NULL,period_name TEXT NOT NULL,grading_scale_id INTEGER NOT NULL,
          grading_scale_version INTEGER NOT NULL,grading_snapshot_json TEXT NOT NULL,
          calculation_snapshot_json TEXT NOT NULL,percentage TEXT NOT NULL,grade TEXT NOT NULL,
          teacher_comments TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'draft',
          issued_at TEXT,issued_by INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(school_id,student_id,academic_year,period_name),
          FOREIGN KEY(grading_scale_id,school_id) REFERENCES assessment_grading_scales(id,school_id));
        CREATE TABLE IF NOT EXISTS assessment_result_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,result_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,actor_id INTEGER NOT NULL,details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS assessment_result_corrections(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,result_id INTEGER NOT NULL,
          proposed_percentage TEXT NOT NULL,proposed_grade TEXT NOT NULL,proposed_comments TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL,requested_by INTEGER NOT NULL,decided_by INTEGER,
          status TEXT NOT NULL DEFAULT 'pending',decision_note TEXT NOT NULL DEFAULT '',
          proposed_subject_results_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,decided_at TEXT);
        CREATE TABLE IF NOT EXISTS assessment_approval_requests(
          id INTEGER PRIMARY KEY AUTOINCREMENT,school_id INTEGER NOT NULL,result_id INTEGER NOT NULL,
          action TEXT NOT NULL,requested_by INTEGER NOT NULL,decided_by INTEGER,
          status TEXT NOT NULL DEFAULT 'pending',reason TEXT NOT NULL DEFAULT '',
          decision_note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,decided_at TEXT,
          FOREIGN KEY(result_id) REFERENCES assessment_results(id));
        CREATE INDEX IF NOT EXISTS idx_assessments_scope ON configurable_assessments(school_id,academic_year,period_name,class_level);
        CREATE INDEX IF NOT EXISTS idx_scores_scope ON assessment_scores(school_id,student_id,assessment_id);
        CREATE INDEX IF NOT EXISTS idx_results_scope ON assessment_results(school_id,student_id,academic_year,period_name,status);
        CREATE TRIGGER IF NOT EXISTS trg_assessment_score_tenant_insert BEFORE INSERT ON assessment_scores
          WHEN NOT EXISTS(SELECT 1 FROM configurable_assessments a WHERE a.id=NEW.assessment_id AND a.school_id=NEW.school_id)
            OR NOT EXISTS(SELECT 1 FROM students s WHERE s.id=NEW.student_id AND s.school_id=NEW.school_id)
          BEGIN SELECT RAISE(ABORT,'assessment score tenant mismatch'); END;
        CREATE TRIGGER IF NOT EXISTS trg_issued_result_immutable BEFORE UPDATE ON assessment_results
          WHEN OLD.status='issued' BEGIN SELECT RAISE(ABORT,'issued results are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS trg_issued_result_no_delete BEFORE DELETE ON assessment_results
          WHEN OLD.status='issued' BEGIN SELECT RAISE(ABORT,'issued results cannot be deleted'); END;
        """)
        correction_columns = {row["name"] for row in db.execute(
            "PRAGMA table_info(assessment_result_corrections)").fetchall()}
        if "proposed_subject_results_json" not in correction_columns:
            db.execute("""ALTER TABLE assessment_result_corrections
                ADD COLUMN proposed_subject_results_json TEXT NOT NULL DEFAULT '[]'""")
        db.commit()
    finally: db.close()

def create_assessment_blueprint(get_db_connection,require_school_context):
    bp=Blueprint("assessment_engine",__name__,url_prefix="/api/assessment")
    def scope():
        sid=require_school_context()
        if not sid: abort(403)
        return int(sid)
    def role(allowed):
        if getattr(current_user,"role","") not in allowed and not getattr(current_user,"is_super_admin",False): abort(403)
    def data():
        obj=request.get_json(silent=True)
        if not isinstance(obj,dict): abort(400,description="JSON object required")
        return obj

    def teacher_assigned(db, school_id, class_level):
        if getattr(current_user,"role","") != "teacher":
            return True
        table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='teacher_class_assignments'").fetchone()
        if table:
            return db.execute("""SELECT 1 FROM teacher_class_assignments
              WHERE school_id=? AND teacher_id=? AND class_level=? AND active=1 LIMIT 1""",
              (school_id,int(current_user.id),class_level)).fetchone() is not None
        return db.execute("""SELECT 1 FROM class_timetable
          WHERE school_id=? AND teacher_id=? AND class_level=? LIMIT 1""",
          (school_id,int(current_user.id),class_level)).fetchone() is not None

    @bp.post("/grading-scales")
    @login_required
    def create_scale():
        sid=scope(); role(ROLES_CONFIGURE); d=data(); name=str(d.get("name","")).strip()
        if not name or len(name)>100: abort(400,description="Grading scale name required")
        try: bands=validate_grade_bands(d.get("bands"))
        except ValueError as e: abort(400,description=str(e))
        db=get_db_connection()
        try:
            version=db.execute("SELECT COALESCE(MAX(version),0)+1 v FROM assessment_grading_scales WHERE school_id=? AND name=?",(sid,name)).fetchone()["v"]
            cur=db.execute("INSERT INTO assessment_grading_scales(school_id,name,version,bands_json,created_by) VALUES(?,?,?,?,?)",(sid,name,version,json.dumps(bands),int(current_user.id)))
            db.commit(); return jsonify(id=cur.lastrowid,name=name,version=version,bands=bands),201
        finally: db.close()

    @bp.get("/grading-scales")
    @login_required
    def list_scales():
        sid=scope(); db=get_db_connection()
        try:
            rows=db.execute("SELECT * FROM assessment_grading_scales WHERE school_id=? ORDER BY name,version DESC",(sid,)).fetchall()
            return jsonify(items=[{"id":r["id"],"name":r["name"],"version":r["version"],"bands":json.loads(r["bands_json"]),"status":r["status"]} for r in rows])
        finally: db.close()

    @bp.post("/assessments")
    @login_required
    def create_assessment():
        sid=scope(); role(ROLES_GRADE); d=data()
        title,subject,level=(str(d.get(k,"")).strip() for k in ("title","subject","class_level"))
        kind=str(d.get("assessment_type","")); year,period=(str(d.get(k,"")).strip() for k in ("academic_year","period_name"))
        if not all((title,subject,level,year,period)) or kind not in ASSESSMENT_TYPES: abort(400,description="Assessment details and supported type are required")
        try:
            weight,maximum=_decimal(d.get("weight"),"weight"),_decimal(d.get("max_score"),"max_score")
            if weight<=0 or maximum<=0 or weight>1000: raise ValueError("Weight and maximum score must be positive")
        except ValueError as e: abort(400,description=str(e))
        db=get_db_connection()
        try:
            if not teacher_assigned(db,sid,level): abort(403,description="Teacher is not assigned to this class")
            cur=db.execute("""INSERT INTO configurable_assessments
              (school_id,campus_id,academic_period_id,title,assessment_type,subject,class_level,academic_year,period_name,weight,max_score,due_date,created_by)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(sid,d.get("campus_id"),d.get("academic_period_id"),title,kind,subject,level,year,period,str(weight),str(maximum),d.get("due_date"),int(current_user.id)))
            db.commit(); return jsonify(id=cur.lastrowid,status="draft"),201
        finally: db.close()

    @bp.post("/assessments/<int:aid>/scores")
    @login_required
    def record_score(aid):
        sid=scope(); role(ROLES_GRADE); d=data()
        try: score=_decimal(d.get("score"),"score")
        except ValueError as e: abort(400,description=str(e))
        db=get_db_connection()
        try:
            a=db.execute("SELECT * FROM configurable_assessments WHERE id=? AND school_id=?",(aid,sid)).fetchone()
            if a is None: abort(404)
            if not teacher_assigned(db,sid,a["class_level"]): abort(403,description="Teacher is not assigned to this class")
            st=db.execute("SELECT id,class_level FROM students WHERE id=? AND school_id=? AND status='active'",(d.get("student_id"),sid)).fetchone()
            if st is None or st["class_level"]!=a["class_level"]: abort(404,description="Student is not in this school's assessment class")
            if score<0 or score>_decimal(a["max_score"],"maximum"): abort(400,description="Score is outside permitted range")
            if a["status"]!="draft": abort(409,description="Assessment is not editable")
            db.execute("""INSERT INTO assessment_scores(school_id,assessment_id,student_id,score,teacher_comment,recorded_by)
              VALUES(?,?,?,?,?,?) ON CONFLICT(school_id,assessment_id,student_id) DO UPDATE SET
              score=excluded.score,teacher_comment=excluded.teacher_comment,recorded_by=excluded.recorded_by,recorded_at=CURRENT_TIMESTAMP""",
              (sid,aid,st["id"],str(score),str(d.get("teacher_comment",""))[:1000],int(current_user.id)))
            db.commit(); return jsonify(saved=True)
        except sqlite3.IntegrityError:
            db.rollback(); abort(400,description="Score violates tenant or assessment constraints")
        finally: db.close()

    @bp.post("/results/calculate")
    @login_required
    def calculate():
        sid=scope(); role(ROLES_GRADE); d=data()
        student_id,year,period,scale_id=d.get("student_id"),str(d.get("academic_year","")),str(d.get("period_name","")),d.get("grading_scale_id")
        db=get_db_connection()
        try:
            student=db.execute("SELECT id FROM students WHERE id=? AND school_id=?",(student_id,sid)).fetchone()
            scale=db.execute("SELECT * FROM assessment_grading_scales WHERE id=? AND school_id=?",(scale_id,sid)).fetchone()
            if student is None or scale is None or not year or not period: abort(404)
            rows=db.execute("""SELECT a.id,a.subject,a.weight,a.max_score,s.score,s.teacher_comment
              FROM configurable_assessments a JOIN assessment_scores s ON s.assessment_id=a.id AND s.school_id=a.school_id
              WHERE a.school_id=? AND s.student_id=? AND a.academic_year=? AND a.period_name=? AND a.status='draft'
              ORDER BY a.subject,a.id""",
              (sid,student_id,year,period)).fetchall()
            if not rows:
                abort(400,description="No scored assessments exist for this student and academic period")
            bands=json.loads(scale["bands_json"])
            grouped={}
            for row in rows:
                grouped.setdefault(row["subject"],[]).append({
                    "assessment_id":row["id"],"weight":row["weight"],
                    "score":row["score"],"max_score":row["max_score"]})
            subject_results=[]
            try:
                for subject, subject_components in sorted(grouped.items()):
                    subject_result=calculate_weighted_result(subject_components,bands)
                    subject_comments="\n".join(
                        row["teacher_comment"] for row in rows
                        if row["subject"]==subject and row["teacher_comment"])
                    subject_results.append({
                        "subject":subject,**subject_result,"comments":subject_comments})
                overall_components=[
                    {"assessment_id":row["id"],"weight":row["weight"],
                     "score":row["score"],"max_score":row["max_score"]}
                    for row in rows]
                overall=calculate_weighted_result(overall_components,bands)
            except ValueError as e:
                abort(400,description=str(e))
            result={"overall":overall,"subjects":subject_results}
            comments="\n".join(r["teacher_comment"] for r in rows if r["teacher_comment"])
            snap={"scale_id":scale["id"],"scale_name":scale["name"],"scale_version":scale["version"],"bands":bands}
            db.execute("""INSERT INTO assessment_results(school_id,student_id,academic_year,period_name,grading_scale_id,grading_scale_version,
              grading_snapshot_json,calculation_snapshot_json,percentage,grade,teacher_comments,status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,'draft')
              ON CONFLICT(school_id,student_id,academic_year,period_name) DO UPDATE SET
              grading_scale_id=excluded.grading_scale_id,grading_scale_version=excluded.grading_scale_version,
              grading_snapshot_json=excluded.grading_snapshot_json,calculation_snapshot_json=excluded.calculation_snapshot_json,
              percentage=excluded.percentage,grade=excluded.grade,teacher_comments=excluded.teacher_comments
              WHERE assessment_results.status='draft'""",
              (sid,student_id,year,period,scale["id"],scale["version"],json.dumps(snap),json.dumps(result),overall["percentage"],overall["grade"],comments))
            db.commit(); return jsonify(result=result,status="draft")
        finally: db.close()

    @bp.post("/results/<int:rid>/request-publication")
    @login_required
    def request_publication(rid):
        sid=scope(); role(ROLES_GRADE); d=data(); db=get_db_connection()
        try:
            row=db.execute("SELECT status FROM assessment_results WHERE id=? AND school_id=?",(rid,sid)).fetchone()
            if row is None: abort(404)
            if row["status"]!="draft": abort(409,description="Only draft results can be submitted")
            cur=db.execute("INSERT INTO assessment_approval_requests(school_id,result_id,action,requested_by,reason) VALUES(?,?,'publish',?,?)",(sid,rid,int(current_user.id),str(d.get("reason",""))[:500]))
            db.execute("UPDATE assessment_results SET status='pending_approval' WHERE id=? AND school_id=?",(rid,sid))
            db.execute("INSERT INTO assessment_result_events(school_id,result_id,event_type,actor_id,details_json) VALUES(?,?,'publication_requested',?,?)",(sid,rid,int(current_user.id),json.dumps({"request_id":cur.lastrowid})))
            db.commit(); return jsonify(request_id=cur.lastrowid,status="pending_approval"),202
        finally: db.close()

    @bp.post("/approvals/<int:reqid>/decision")
    @login_required
    def decide(reqid):
        sid=scope(); role(ROLES_APPROVE); d=data(); decision=str(d.get("decision",""))
        if decision not in {"approved","rejected"}: abort(400,description="Decision must be approved or rejected")
        db=get_db_connection()
        try:
            req=db.execute("SELECT * FROM assessment_approval_requests WHERE id=? AND school_id=?",(reqid,sid)).fetchone()
            if req is None: abort(404)
            if req["status"]!="pending" or int(req["requested_by"])==int(current_user.id): abort(409,description="Self-approval or repeat decision is prohibited")
            result=db.execute("SELECT * FROM assessment_results WHERE id=? AND school_id=?",(req["result_id"],sid)).fetchone()
            if result is None or result["status"]!="pending_approval": abort(409)
            now=datetime.now(timezone.utc).isoformat()
            db.execute("UPDATE assessment_approval_requests SET status=?,decided_by=?,decision_note=?,decided_at=? WHERE id=? AND school_id=? AND status='pending'",(decision,int(current_user.id),str(d.get("note",""))[:500],now,reqid,sid))
            if decision=="approved":
                db.execute("UPDATE assessment_results SET status='issued',issued_at=?,issued_by=? WHERE id=? AND school_id=? AND status='pending_approval'",(now,int(current_user.id),result["id"],sid))
            else: db.execute("UPDATE assessment_results SET status='draft' WHERE id=? AND school_id=?",(result["id"],sid))
            db.execute("INSERT INTO assessment_result_events(school_id,result_id,event_type,actor_id,details_json) VALUES(?,?,?,?,?)",(sid,result["id"],"result_"+decision,int(current_user.id),json.dumps({"request_id":reqid})))
            db.commit(); return jsonify(status=decision)
        finally: db.close()

    @bp.post("/results/<int:rid>/corrections")
    @login_required
    def request_correction(rid):
        sid=scope(); role(ROLES_GRADE); d=data()
        reason=str(d.get("reason","")).strip()
        grade=str(d.get("grade","")).strip()
        try: pct=_decimal(d.get("percentage"),"percentage")
        except ValueError as e: abort(400,description=str(e))
        if pct<0 or pct>100 or not grade or not reason: abort(400,description="Valid percentage, grade and correction reason are required")
        db=get_db_connection()
        try:
            result=db.execute("SELECT * FROM assessment_results WHERE id=? AND school_id=?",(rid,sid)).fetchone()
            if result is None: abort(404)
            if result["status"]!="issued": abort(409,description="Only issued results can be corrected")
            frozen_scale=json.loads(result["grading_snapshot_json"])
            try:
                expected_band=grade_band_for_percentage(pct,frozen_scale["bands"])
            except (KeyError,ValueError) as e:
                abort(400,description=str(e))
            if expected_band is None or grade!=expected_band["grade"]:
                abort(400,description="The proposed overall grade must match the frozen grading scale")
            calculation=json.loads(result["calculation_snapshot_json"])
            original_subjects=calculation.get("subjects",[])
            proposed_subjects=d.get("subject_results",original_subjects)
            if not isinstance(proposed_subjects,list):
                abort(400,description="subject_results must be a list")
            original_names={item.get("subject") for item in original_subjects}
            proposed_names=set()
            validated_subjects=[]
            for item in proposed_subjects:
                if not isinstance(item,dict):
                    abort(400,description="Each subject correction must be an object")
                subject=str(item.get("subject","")).strip()
                if not subject or subject in proposed_names:
                    abort(400,description="Subject corrections must have unique subject names")
                try:
                    subject_pct=_decimal(item.get("percentage"),"subject percentage")
                    subject_band=grade_band_for_percentage(subject_pct,frozen_scale["bands"])
                except ValueError as e:
                    abort(400,description=str(e))
                subject_grade=str(item.get("grade","")).strip()
                if subject_band is None or subject_grade!=subject_band["grade"]:
                    abort(400,description=f"Grade does not match the frozen scale for {subject}")
                if subject not in original_names:
                    abort(400,description=f"Unknown subject in correction: {subject}")
                proposed_names.add(subject)
                validated_subjects.append({
                    "subject":subject,"percentage":str(subject_pct),
                    "grade":subject_grade,"remark":subject_band["remark"],
                    "comments":str(item.get("comments",""))[:2000]})
            if proposed_names!=original_names:
                abort(400,description="Corrections must retain every subject in the issued result")
            cur=db.execute("""INSERT INTO assessment_result_corrections
              (school_id,result_id,proposed_percentage,proposed_grade,proposed_comments,reason,requested_by,proposed_subject_results_json)
              VALUES(?,?,?,?,?,?,?,?)""",
              (sid,rid,str(pct),grade,str(d.get("comments",""))[:2000],reason[:1000],
               int(current_user.id),json.dumps(validated_subjects)))
            db.execute("""INSERT INTO assessment_result_events
              (school_id,result_id,event_type,actor_id,details_json) VALUES(?,?,'correction_requested',?,?)""",
              (sid,rid,int(current_user.id),json.dumps({"correction_id":cur.lastrowid,"reason":reason[:1000]})))
            db.commit(); return jsonify(correction_id=cur.lastrowid,status="pending"),202
        finally: db.close()

    @bp.post("/corrections/<int:cid>/decision")
    @login_required
    def decide_correction(cid):
        sid=scope(); role(ROLES_APPROVE); d=data()
        decision=str(d.get("decision",""))
        if decision not in {"approved","rejected"}: abort(400,description="Decision must be approved or rejected")
        db=get_db_connection()
        try:
            correction=db.execute("SELECT * FROM assessment_result_corrections WHERE id=? AND school_id=?",(cid,sid)).fetchone()
            if correction is None: abort(404)
            if correction["status"]!="pending" or int(correction["requested_by"])==int(current_user.id):
                abort(409,description="Correction is already decided or self-approval is prohibited")
            now=datetime.now(timezone.utc).isoformat()
            db.execute("""UPDATE assessment_result_corrections SET status=?,decided_by=?,decision_note=?,decided_at=?
              WHERE id=? AND school_id=? AND status='pending'""",
              (decision,int(current_user.id),str(d.get("note",""))[:500],now,cid,sid))
            db.execute("""INSERT INTO assessment_result_events
              (school_id,result_id,event_type,actor_id,details_json) VALUES(?,?,?,?,?)""",
              (sid,correction["result_id"],"correction_"+decision,int(current_user.id),json.dumps({"correction_id":cid})))
            db.commit(); return jsonify(status=decision)
        finally: db.close()

    @bp.get("/students/<int:student_id>/transcript")
    @login_required
    def transcript(student_id):
        sid=scope(); role(ROLES_GRADE); db=get_db_connection()
        try:
            rows=db.execute("""SELECT r.id,r.academic_year,r.period_name,
              COALESCE((SELECT c.proposed_percentage FROM assessment_result_corrections c
                WHERE c.result_id=r.id AND c.school_id=r.school_id AND c.status='approved'
                ORDER BY c.id DESC LIMIT 1),r.percentage) AS percentage,
              COALESCE((SELECT c.proposed_grade FROM assessment_result_corrections c
                WHERE c.result_id=r.id AND c.school_id=r.school_id AND c.status='approved'
                ORDER BY c.id DESC LIMIT 1),r.grade) AS grade,
              COALESCE((SELECT c.proposed_comments FROM assessment_result_corrections c
                WHERE c.result_id=r.id AND c.school_id=r.school_id AND c.status='approved'
                ORDER BY c.id DESC LIMIT 1),r.teacher_comments) AS comments,
              (SELECT c.proposed_subject_results_json FROM assessment_result_corrections c
                WHERE c.result_id=r.id AND c.school_id=r.school_id AND c.status='approved'
                ORDER BY c.id DESC LIMIT 1) AS corrected_subject_results,
              r.calculation_snapshot_json,r.issued_at,r.grading_scale_version,r.grading_snapshot_json FROM assessment_results r
              JOIN students s ON s.id=r.student_id AND s.school_id=r.school_id
              WHERE r.school_id=? AND r.student_id=? AND r.status='issued' ORDER BY r.academic_year,r.period_name""",(sid,student_id)).fetchall()
            transcript_results=[]
            for r in rows:
                calculation=json.loads(r["calculation_snapshot_json"])
                subjects=calculation.get("subjects",[])
                if r["corrected_subject_results"] is not None:
                    subjects=json.loads(r["corrected_subject_results"])
                overall=calculation.get("overall",calculation)
                transcript_results.append({
                    "academic_year":r["academic_year"],"period":r["period_name"],
                    "percentage":r["percentage"],"grade":r["grade"],"comments":r["comments"],
                    "subjects":subjects,"overall":overall,
                    "issued_at":r["issued_at"],"grading_scale_version":r["grading_scale_version"],
                    "grading_snapshot":json.loads(r["grading_snapshot_json"]),"result_id":r["id"]})
            return jsonify(student_id=student_id,results=transcript_results)
        finally: db.close()
    return bp
