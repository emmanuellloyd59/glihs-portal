from datetime import date, timedelta

def register_school_performance_routes(ns):
    app=ns["app"]; login_required=ns["login_required"]; current_user=ns["current_user"]
    render_template=ns["render_template"]; abort=ns["abort"]; dbopen=ns["get_db_connection"]; school_context=ns["require_school_context"]
    leaders={"super_admin","school_admin","principal","vice_principal","academic_admin","registrar"}
    attendance_roles=leaders|{"attendance_officer"}
    finance_roles={"super_admin","school_admin","principal","registrar","finance_admin"}
    @app.route("/school-performance")
    @login_required
    def school_performance():
        role=current_user.role
        if role not in attendance_roles|{"finance_admin"}: abort(403)
        sid=school_context(); today=date.today(); start=today-timedelta(days=13); year=f"{today.year}-{today.year+1}"
        se=role in leaders or role=="attendance_officer"; sa=role in attendance_roles; sg=role in leaders; sf=role in finance_roles
        db=dbopen()
        try:
            school=db.execute("SELECT name FROM schools WHERE id=? AND status='active'",(sid,)).fetchone()
            if not school: abort(403)
            enrollment={"active":0,"total":0,"recent":0,"classes":[]}
            attendance={"daily":[],"present":0,"absent":0,"late":0,"excused":0,"total":0,"rate":0.0}
            academics={"average":None,"assessments":0,"students":0}
            finances={"total_fees":0.0,"paid":0.0,"balance":0.0,"accounts":0,"paid_accounts":0,"partial_accounts":0,"unpaid_accounts":0,"academic_year":year}
            if se:
                enrollment["active"]=db.execute("SELECT COUNT(*) FROM students WHERE school_id=? AND status='active'",(sid,)).fetchone()[0]
                enrollment["total"]=db.execute("SELECT COUNT(*) FROM students WHERE school_id=?",(sid,)).fetchone()[0]
                enrollment["recent"]=db.execute("SELECT COUNT(*) FROM students WHERE school_id=? AND date(COALESCE(enrollment_date,created_at))>=date(?)",(sid,start.isoformat())).fetchone()[0]
                enrollment["classes"]=db.execute("SELECT class_level,COUNT(*) student_count FROM students WHERE school_id=? AND status='active' GROUP BY class_level ORDER BY class_level COLLATE NOCASE LIMIT 16",(sid,)).fetchall()
            if sa:
                rows=db.execute("""SELECT attendance_date,SUM(CASE WHEN lower(status)='present' THEN 1 ELSE 0 END) present,SUM(CASE WHEN lower(status)='absent' THEN 1 ELSE 0 END) absent,SUM(CASE WHEN lower(status)='late' THEN 1 ELSE 0 END) late,SUM(CASE WHEN lower(status)='excused' THEN 1 ELSE 0 END) excused,COUNT(*) total FROM attendance WHERE school_id=? AND attendance_date BETWEEN ? AND ? GROUP BY attendance_date ORDER BY attendance_date""",(sid,start.isoformat(),today.isoformat())).fetchall()
                attendance["daily"]=rows
                for r in rows:
                    for k in ("present","absent","late","excused","total"): attendance[k]+=int(r[k] or 0)
                n=attendance["total"]-attendance["excused"]; attendance["rate"]=round((attendance["present"]+attendance["late"])*100/n,1) if n else 0.0
            if sg:
                r=db.execute("SELECT AVG(score*100.0/NULLIF(max_score,0)) average,COUNT(*) assessments,COUNT(DISTINCT student_id) students FROM grades WHERE school_id=? AND max_score>0",(sid,)).fetchone()
                academics={"average":round(float(r["average"]),1) if r["average"] is not None else None,"assessments":int(r["assessments"] or 0),"students":int(r["students"] or 0)}
            if sf:
                r=db.execute("""SELECT COALESCE(SUM(total_fee),0) total_fees,COALESCE(SUM(amount_paid),0) paid,COALESCE(SUM(total_fee-amount_paid),0) balance,COUNT(*) accounts,SUM(CASE WHEN amount_paid>=total_fee THEN 1 ELSE 0 END) paid_accounts,SUM(CASE WHEN amount_paid>0 AND amount_paid<total_fee THEN 1 ELSE 0 END) partial_accounts,SUM(CASE WHEN amount_paid=0 AND total_fee>0 THEN 1 ELSE 0 END) unpaid_accounts FROM student_finances WHERE school_id=? AND academic_year=?""",(sid,year)).fetchone()
                for k in ("total_fees","paid","balance"): finances[k]=float(r[k] or 0)
                for k in ("accounts","paid_accounts","partial_accounts","unpaid_accounts"): finances[k]=int(r[k] or 0)
        finally: db.close()
        return render_template("school_performance.html",school_name=school["name"],enrollment=enrollment,attendance=attendance,academics=academics,finances=finances,show_enrollment=se,show_attendance=sa,show_academics=sg,show_finances=sf,start_date=start.isoformat(),end_date=today.isoformat())
