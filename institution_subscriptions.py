"""Institution lifecycle, entitlements and provider-neutral subscription foundation.

This module never collects money, contacts a payment provider, or changes legacy
school lifecycle columns. Billing state is advisory until an explicit platform
owner transition is recorded. School records are never deleted by billing.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

INSTITUTION_STATES = {"trial", "active", "restricted", "suspended", "cancelled", "archived"}
SUBSCRIPTION_STATES = {"trial", "active", "past_due", "restricted", "suspended", "cancelled"}
BILLING_PERIODS = {"monthly", "quarterly", "annually", "custom"}
ACCESS = {
    "trial": "plan",
    "active": "plan",
    "restricted": "read_only",
    "suspended": "essential_only",
    "cancelled": "read_only",
    "archived": "none",
}
DEFAULT_GRACE_DAYS = 14


def _stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def initialize_subscription_schema(connection_factory):
    """Create additive, idempotent tables; do not update existing institutions."""
    db = connection_factory()
    try:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS institution_lifecycle (
          school_id INTEGER PRIMARY KEY REFERENCES schools(id),
          state TEXT NOT NULL DEFAULT 'active'
            CHECK(state IN ('trial','active','restricted','suspended','cancelled','archived')),
          reason TEXT, changed_by INTEGER REFERENCES users(id),
          changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS subscription_plans (
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          billing_period TEXT NOT NULL CHECK(billing_period IN ('monthly','quarterly','annually','custom')),
          amount_minor INTEGER NOT NULL DEFAULT 0 CHECK(amount_minor>=0),
          currency TEXT NOT NULL DEFAULT 'USD', trial_days INTEGER NOT NULL DEFAULT 0 CHECK(trial_days>=0),
          grace_days INTEGER NOT NULL DEFAULT 14 CHECK(grace_days>=0),
          usage_limits_json TEXT NOT NULL DEFAULT '{}',
          entitlements_json TEXT NOT NULL DEFAULT '{}',
          active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS institution_subscriptions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
          status TEXT NOT NULL CHECK(status IN ('trial','active','past_due','restricted','suspended','cancelled')),
          billing_period TEXT NOT NULL CHECK(billing_period IN ('monthly','quarterly','annually','custom')),
          currency TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor>=0),
          period_start TEXT NOT NULL, period_end TEXT NOT NULL, trial_ends_at TEXT,
          grace_ends_at TEXT, cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
          provider_key TEXT, provider_customer_ref TEXT, provider_subscription_ref TEXT,
          created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_subscriptions_school ON institution_subscriptions(school_id,status);
        CREATE TABLE IF NOT EXISTS subscription_entitlements (
          id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id INTEGER NOT NULL REFERENCES institution_subscriptions(id),
          entitlement_key TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
          limit_value INTEGER, UNIQUE(subscription_id,entitlement_key)
        );
        CREATE TABLE IF NOT EXISTS billing_operations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL UNIQUE,
          operation_type TEXT NOT NULL, school_id INTEGER REFERENCES schools(id),
          provider_key TEXT, provider_event_ref TEXT, request_hash TEXT NOT NULL,
          result_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS billing_invoices (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER NOT NULL REFERENCES schools(id),
          subscription_id INTEGER NOT NULL REFERENCES institution_subscriptions(id),
          invoice_number TEXT NOT NULL UNIQUE, amount_minor INTEGER NOT NULL CHECK(amount_minor>=0),
          currency TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','open','paid','void','uncollectible')),
          due_at TEXT, paid_at TEXT, provider_key TEXT, provider_invoice_ref TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS billing_provider_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, provider_key TEXT NOT NULL,
          event_ref TEXT NOT NULL, event_type TEXT NOT NULL, payload_hash TEXT NOT NULL,
          processed_at TEXT, outcome TEXT, UNIQUE(provider_key,event_ref)
        );
        CREATE TABLE IF NOT EXISTS subscription_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id INTEGER REFERENCES schools(id),
          actor_user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
          before_json TEXT, after_json TEXT, reason TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        db.commit()
    finally:
        db.close()


def _platform(connection_factory):
    from authorization import require_permission
    require_permission("platform.manage_schools", connection_factory)


def _school_view(connection_factory, school_id):
    from authorization import require_permission
    require_permission("school.view_dashboard", connection_factory, school_id=school_id)


def effective_access(connection, school_id, now=None):
    """Return access policy without mutating subscription or institution state."""
    now = now or datetime.now(timezone.utc)
    row = connection.execute(
        """SELECT l.state AS institution_state,s.status,s.period_end,s.grace_ends_at,
                  p.entitlements_json,p.usage_limits_json
           FROM institution_lifecycle l
           LEFT JOIN institution_subscriptions s ON s.school_id=l.school_id
             AND s.id=(SELECT MAX(s2.id) FROM institution_subscriptions s2 WHERE s2.school_id=l.school_id)
           LEFT JOIN subscription_plans p ON p.id=s.plan_id
           WHERE l.school_id=?""", (school_id,)
    ).fetchone()
    if row is None:
        # Existing institutions are not implicitly enrolled in billing.
        return {"state":"active","access":"legacy_unmetered","entitlements":{},"limits":{}}
    state = row["institution_state"]
    if state == "archived":
        access = "none"
    elif state in {"suspended","cancelled"}:
        access = ACCESS[state]
    elif row["status"] == "past_due":
        grace = row["grace_ends_at"]
        if grace and datetime.fromisoformat(grace) < now:
            access = "read_only"
        else:
            access = "plan"
    else:
        access = ACCESS.get(state, "plan")
    return {"state":state,"subscription_status":row["status"],"access":access,
            "entitlements":json.loads(row["entitlements_json"] or "{}"),
            "limits":json.loads(row["usage_limits_json"] or "{}")}


def create_subscription_blueprint(connection_factory):
    bp = Blueprint("institution_subscriptions", __name__)

    @bp.get("/api/v1/platform/subscription-plans")
    @login_required
    def list_plans():
        _platform(connection_factory)
        db=connection_factory()
        try:
            rows=db.execute("SELECT * FROM subscription_plans ORDER BY id").fetchall()
            return jsonify({"data":[dict(x) for x in rows]})
        finally: db.close()

    @bp.post("/api/v1/platform/subscription-plans")
    @login_required
    def save_plan():
        _platform(connection_factory)
        p=request.get_json(silent=True)
        if not isinstance(p,dict): return jsonify({"error":"JSON object required"}),400
        code=str(p.get("code","")).strip().lower()
        name=str(p.get("name","")).strip()
        period=p.get("billing_period","monthly")
        currency=str(p.get("currency","USD")).upper()
        amount=p.get("amount_minor",0); trial=p.get("trial_days",0); grace=p.get("grace_days",DEFAULT_GRACE_DAYS)
        ent=p.get("entitlements",{}); limits=p.get("usage_limits",{})
        if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,49}",code) or len(name)<2
            or period not in BILLING_PERIODS or not re.fullmatch(r"[A-Z]{3}",currency)
            or type(amount) is not int or amount<0 or type(trial) is not int or trial<0
            or type(grace) is not int or grace<0 or not isinstance(ent,dict) or not isinstance(limits,dict)):
            return jsonify({"error":"Invalid plan fields"}),422
        db=connection_factory()
        try:
            db.execute("""INSERT INTO subscription_plans(code,name,description,billing_period,amount_minor,currency,
                       trial_days,grace_days,usage_limits_json,entitlements_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET
                       name=excluded.name,description=excluded.description,billing_period=excluded.billing_period,
                       amount_minor=excluded.amount_minor,currency=excluded.currency,trial_days=excluded.trial_days,
                       grace_days=excluded.grace_days,usage_limits_json=excluded.usage_limits_json,
                       entitlements_json=excluded.entitlements_json,updated_at=CURRENT_TIMESTAMP""",
                       (code,name,str(p.get("description",""))[:1000],period,amount,currency,trial,grace,_json(limits),_json(ent)))
            db.execute("INSERT INTO subscription_audit(actor_user_id,action,after_json) VALUES(?,?,?)",
                       (int(current_user.id),"plan.upserted",_json({"code":code,"billing_period":period})))
            db.commit()
            return jsonify({"status":"saved","code":code}),201
        finally: db.close()

    @bp.post("/api/v1/platform/schools/<int:school_id>/subscription")
    @login_required
    def assign_subscription(school_id):
        _platform(connection_factory)
        p=request.get_json(silent=True)
        if not isinstance(p,dict): return jsonify({"error":"JSON object required"}),400
        key=str(request.headers.get("Idempotency-Key","")).strip()
        if not key or len(key)>180: return jsonify({"error":"Idempotency-Key header is required"}),400
        plan_code=str(p.get("plan_code","")).strip().lower()
        start=str(p.get("period_start") or _stamp())
        end=str(p.get("period_end",""))
        status=p.get("status","trial")
        if status not in {"trial","active"}: return jsonify({"error":"New subscriptions may only start as trial or active"}),422
        try:
            if datetime.fromisoformat(end.replace("Z","+00:00")) <= datetime.fromisoformat(start.replace("Z","+00:00")): raise ValueError()
        except ValueError: return jsonify({"error":"period_end must be after period_start"}),422
        db=connection_factory()
        try:
            import hashlib
            payload_hash=hashlib.sha256(_json(p).encode()).hexdigest()
            prior=db.execute("SELECT request_hash,result_json FROM billing_operations WHERE idempotency_key=?",(key,)).fetchone()
            if prior:
                if prior["request_hash"]!=payload_hash: return jsonify({"error":"Idempotency key reused with different request"}),409
                return jsonify(json.loads(prior["result_json"])),200
            plan=db.execute("SELECT * FROM subscription_plans WHERE code=? AND active=1",(plan_code,)).fetchone()
            school=db.execute("SELECT id FROM schools WHERE id=?",(school_id,)).fetchone()
            if not plan or not school: abort(404)
            db.execute("BEGIN IMMEDIATE")
            cursor=db.execute("""INSERT INTO institution_subscriptions
                (school_id,plan_id,status,billing_period,currency,amount_minor,period_start,period_end,
                 trial_ends_at,grace_ends_at,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (school_id,plan["id"],status,plan["billing_period"],plan["currency"],plan["amount_minor"],
                 start,end,p.get("trial_ends_at"),p.get("grace_ends_at"),int(current_user.id)))
            sub_id=cursor.lastrowid
            for ent,enabled in json.loads(plan["entitlements_json"]).items():
                db.execute("INSERT INTO subscription_entitlements(subscription_id,entitlement_key,enabled) VALUES(?,?,?)",
                           (sub_id,str(ent),1 if enabled is True else 0))
            db.execute("INSERT OR IGNORE INTO institution_lifecycle(school_id,state,changed_by) VALUES(?,?,?)",
                       (school_id,status,int(current_user.id)))
            result={"status":"created","subscription_id":sub_id,"school_id":school_id}
            db.execute("""INSERT INTO billing_operations(idempotency_key,operation_type,school_id,request_hash,result_json)
                          VALUES(?,?,?,?,?)""",(key,"subscription.assign",school_id,payload_hash,_json(result)))
            db.execute("INSERT INTO subscription_audit(school_id,actor_user_id,action,after_json) VALUES(?,?,?,?)",
                       (school_id,int(current_user.id),"subscription.assigned",_json(result)))
            db.commit()
            return jsonify(result),201
        except Exception:
            db.rollback(); raise
        finally: db.close()

    @bp.get("/api/v1/schools/<int:school_id>/subscription")
    @login_required
    def school_subscription(school_id):
        _school_view(connection_factory,school_id)
        db=connection_factory()
        try:
            row=db.execute("""SELECT s.id,s.status,s.billing_period,s.currency,s.amount_minor,s.period_start,
                       s.period_end,s.trial_ends_at,s.grace_ends_at,s.cancel_at_period_end,p.name AS plan_name,
                       p.entitlements_json,p.usage_limits_json
                       FROM institution_subscriptions s JOIN subscription_plans p ON p.id=s.plan_id
                       WHERE s.school_id=? ORDER BY s.id DESC LIMIT 1""",(school_id,)).fetchone()
            if not row: return jsonify({"data":None,"access":{"state":"active","access":"legacy_unmetered"}})
            return jsonify({"data":dict(row),"access":effective_access(db,school_id)})
        finally: db.close()

    @bp.get("/api/v1/platform/schools/<int:school_id>/subscription")
    @login_required
    def platform_school_subscription(school_id):
        _platform(connection_factory)
        db=connection_factory()
        try:
            rows=db.execute("SELECT * FROM institution_subscriptions WHERE school_id=? ORDER BY id DESC",(school_id,)).fetchall()
            lifecycle=db.execute("SELECT * FROM institution_lifecycle WHERE school_id=?",(school_id,)).fetchone()
            return jsonify({"subscriptions":[dict(x) for x in rows],"lifecycle":dict(lifecycle) if lifecycle else None,
                            "access":effective_access(db,school_id)})
        finally: db.close()

    @bp.post("/api/v1/platform/schools/<int:school_id>/lifecycle")
    @login_required
    def change_lifecycle(school_id):
        _platform(connection_factory)
        p=request.get_json(silent=True)
        state=p.get("state") if isinstance(p,dict) else None
        reason=str(p.get("reason","")).strip()[:500] if isinstance(p,dict) else ""
        if state not in INSTITUTION_STATES or not reason:
            return jsonify({"error":"A valid state and audit reason are required"}),422
        # State changes are explicit platform-owner actions; billing never invokes this route.
        db=connection_factory()
        try:
            if not db.execute("SELECT 1 FROM schools WHERE id=?",(school_id,)).fetchone(): abort(404)
            old=db.execute("SELECT state FROM institution_lifecycle WHERE school_id=?",(school_id,)).fetchone()
            before=old["state"] if old else "active"
            db.execute("""INSERT INTO institution_lifecycle(school_id,state,reason,changed_by,changed_at,version)
                VALUES(?,?,?,?,?,1) ON CONFLICT(school_id) DO UPDATE SET state=excluded.state,
                reason=excluded.reason,changed_by=excluded.changed_by,changed_at=excluded.changed_at,
                version=institution_lifecycle.version+1""",(school_id,state,reason,int(current_user.id),_stamp()))
            db.execute("INSERT INTO subscription_audit(school_id,actor_user_id,action,before_json,after_json,reason) VALUES(?,?,?,?,?,?)",
                       (school_id,int(current_user.id),"institution.lifecycle_changed",_json({"state":before}),
                        _json({"state":state}),reason))
            db.commit()
            return jsonify({"school_id":school_id,"state":state})
        finally: db.close()

    @bp.get("/api/v1/schools/<int:school_id>/subscription/access")
    @login_required
    def subscription_access(school_id):
        _school_view(connection_factory,school_id)
        db=connection_factory()
        try: return jsonify(effective_access(db,school_id))
        finally: db.close()

    return bp
