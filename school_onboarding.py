"""International school onboarding layered onto the existing School Network.

Draft applications are separate from schools. A school row is created only after
submission passes the active country profile. All schema changes are additive.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SERVICES = (
    ("school_website", "School Website"),
    ("school_management", "School Management"),
    ("learning_management", "Learning Management"),
    ("education_finance", "Education Finance"),
    ("communication", "Communication"),
    ("analytics_ai", "Analytics and AI"),
    ("education_marketplace", "Education Marketplace"),
)


def _now():
    return datetime.now(timezone.utc)


def _stamp(value=None):
    return (value or _now()).isoformat(timespec="seconds")


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _secret():
    return str(current_app.config["SECRET_KEY"]).encode("utf-8")


def _rate_key(value):
    return hmac.new(_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def _clean(value, limit=255):
    return str(value or "").strip()[:limit]


def initialize_onboarding_schema(connection_factory):
    connection = connection_factory()
    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS country_requirement_profiles (
            country_code TEXT PRIMARY KEY,
            country_name TEXT NOT NULL,
            region_label TEXT NOT NULL DEFAULT 'Administrative region',
            region_required INTEGER NOT NULL DEFAULT 0 CHECK(region_required IN (0,1)),
            registration_number_required INTEGER NOT NULL DEFAULT 0 CHECK(registration_number_required IN (0,1)),
            requirements_json TEXT NOT NULL DEFAULT '{}',
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS onboarding_service_catalog (
            service_key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS onboarding_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            applicant_email TEXT NOT NULL,
            resume_token_hash TEXT NOT NULL,
            country_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','submitted','reviewing','approved','activated','configuring','completed','rejected')),
            payload_json TEXT NOT NULL DEFAULT '{}',
            school_id INTEGER REFERENCES schools(id),
            expires_at TEXT NOT NULL,
            submitted_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_onboarding_applicant ON onboarding_applications(applicant_email, created_at);
        CREATE INDEX IF NOT EXISTS idx_onboarding_status ON onboarding_applications(status, updated_at);
        CREATE TABLE IF NOT EXISTS onboarding_rate_limits (
            rate_key TEXT PRIMARY KEY,
            window_started_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            blocked_until TEXT
        );
        CREATE TABLE IF NOT EXISTS onboarding_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER,
            school_id INTEGER,
            actor_user_id INTEGER,
            event_type TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_onboarding_events_school ON onboarding_events(school_id, created_at);
        CREATE TABLE IF NOT EXISTS school_service_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            service_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'requested',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(school_id, service_key)
        );
        CREATE TABLE IF NOT EXISTS school_admin_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            accepted_at TEXT,
            accepted_user_id INTEGER,
            invited_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_school_admin_invites ON school_admin_invitations(school_id, email, expires_at);
        CREATE TABLE IF NOT EXISTS onboarding_notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT
        );
        """)
        app_columns = {row[1] for row in connection.execute("PRAGMA table_info(onboarding_applications)")}
        if "verification_token_hash" not in app_columns:
            connection.execute("ALTER TABLE onboarding_applications ADD COLUMN verification_token_hash TEXT")
        if "email_verified" not in app_columns:
            connection.execute("ALTER TABLE onboarding_applications ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        for code, label in SERVICES:
            connection.execute(
                "INSERT OR IGNORE INTO onboarding_service_catalog(service_key,label) VALUES (?,?)",
                (code, label),
            )
        connection.execute(
            """INSERT OR IGNORE INTO country_requirement_profiles
               (country_code,country_name,region_label,region_required,registration_number_required,requirements_json)
               VALUES ('*','Other / not yet configured','Administrative region',0,0,'{}')"""
        )
        # Country choices are reference data only. Legal requirements remain
        # configurable by platform owners and are not inferred from country.
        country_choices = [
            ("LR","Liberia"),("GH","Ghana"),("NG","Nigeria"),("SL","Sierra Leone"),
            ("CI","Cote d'Ivoire"),("GM","The Gambia"),("SN","Senegal"),("KE","Kenya"),
            ("UG","Uganda"),("TZ","Tanzania"),("ZA","South Africa"),("RW","Rwanda"),
            ("ET","Ethiopia"),("US","United States"),("GB","United Kingdom"),
            ("CA","Canada"),("AU","Australia"),("IN","India"),("AE","United Arab Emirates"),
            ("FR","France"),("DE","Germany"),("BR","Brazil"),("JP","Japan")
        ]
        for code, name in country_choices:
            connection.execute(
                """INSERT OR IGNORE INTO country_requirement_profiles
                   (country_code,country_name,region_label,region_required,registration_number_required,requirements_json)
                   VALUES (?,?, 'Administrative region',0,0,'{}')""",(code,name)
            )
        connection.commit()
    finally:
        connection.close()


def _record_event(connection, application_id, school_id, actor_id, event_type, details=None):
    connection.execute(
        """INSERT INTO onboarding_events
           (application_id,school_id,actor_user_id,event_type,details_json)
           VALUES (?,?,?,?,?)""",
        (application_id, school_id, actor_id, event_type,
         json.dumps(details or {}, sort_keys=True)[:5000]),
    )


def _queue_email(connection, recipient, subject, body):
    if recipient and EMAIL_RE.fullmatch(recipient):
        connection.execute(
            "INSERT INTO onboarding_notification_outbox(recipient,subject,body) VALUES (?,?,?)",
            (recipient, subject[:180], body[:10000]),
        )


def _country_profile(connection, code):
    row = connection.execute(
        "SELECT * FROM country_requirement_profiles WHERE country_code=? AND active=1", (code,)
    ).fetchone()
    if row is None:
        row = connection.execute(
            "SELECT * FROM country_requirement_profiles WHERE country_code='*' AND active=1"
        ).fetchone()
    return dict(row) if row else None


def _validate_payload(payload, profile, services):
    errors = []
    name = _clean(payload.get("name"), 160)
    email = _clean(payload.get("contact_email") or payload.get("administrator_email"), 254).lower()
    country = _clean(payload.get("country_code"), 2).upper()
    region = _clean(payload.get("region") or payload.get("county"), 100)
    if len(name) < 2:
        errors.append("Institution name must contain at least two characters.")
    if not EMAIL_RE.fullmatch(email):
        errors.append("A valid institutional or administrator email is required.")
    if not re.fullmatch(r"[A-Z]{2}", country):
        errors.append("Select a valid two-letter country code.")
    if profile and profile["region_required"] and not region:
        errors.append(profile["region_label"] + " is required for this country.")
    if profile and profile["registration_number_required"] and not _clean(payload.get("registration_number"), 100):
        errors.append("Institutional registration or license number is required for this country.")
    configured = json.loads(profile["requirements_json"] or "{}") if profile else {}
    required_fields = configured.get("required_fields", []) if isinstance(configured, dict) else []
    field_labels = {
        "registration_number": "Institutional registration number",
        "region": profile["region_label"] if profile else "Administrative region",
        "city": "City", "owner_name": "Responsible owner or authority",
        "contact_phone": "Institutional phone", "website_url": "Official website",
    }
    for field in required_fields if isinstance(required_fields, list) else []:
        if field in field_labels and not _clean(payload.get(field), 255):
            errors.append(field_labels[field] + " is required for this country.")
    requested = payload.get("requested_services", [])
    if not isinstance(requested, list) or not requested:
        errors.append("Select at least one requested service.")
        requested = []
    allowed = {row["service_key"] for row in services}
    if any(not isinstance(item, str) or item not in allowed for item in requested):
        errors.append("One or more requested services are unavailable.")
    if payload.get("institution_type", "independent") not in {"independent", "group_affiliated"}:
        errors.append("Choose a valid institution type.")
    if payload.get("ownership_type") and payload["ownership_type"] not in {
        "government","private","faith_based","community","nonprofit","public_private","other"
    }:
        errors.append("Choose a valid ownership type.")
    if payload.get("contact_email") and not EMAIL_RE.fullmatch(_clean(payload.get("contact_email"),254).lower()):
        errors.append("Institutional contact email is invalid.")
    if payload.get("website_url") and not re.fullmatch(r"https?://[^\s]+", _clean(payload.get("website_url"),255), re.I):
        errors.append("Website URL must start with http:// or https://.")
    return {
        "name": name, "legal_name": _clean(payload.get("legal_name") or name,200),
        "contact_email": email, "country_code": country, "region": region,
        "city": _clean(payload.get("city"),100), "owner_name": _clean(payload.get("owner_name") or payload.get("administrator_name"),160),
        "requested_services": sorted(set(requested)),
    }, errors


def create_school_onboarding_blueprint(connection_factory, platform_owner_check):
    bp = Blueprint("school_onboarding", __name__)

    def _json_error(code, message, status):
        return jsonify({"error": {"code": code, "message": message}}), status

    def _rate_limit(connection, key, limit=8, window_minutes=30):
        now = _now()
        row = connection.execute("SELECT * FROM onboarding_rate_limits WHERE rate_key=?", (key,)).fetchone()
        if row and row["blocked_until"] and row["blocked_until"] > _stamp(now):
            return False
        if not row or now - datetime.fromisoformat(row["window_started_at"]) > timedelta(minutes=window_minutes):
            connection.execute(
                """INSERT INTO onboarding_rate_limits(rate_key,window_started_at,attempts,blocked_until)
                   VALUES (?,?,1,NULL) ON CONFLICT(rate_key) DO UPDATE SET
                   window_started_at=excluded.window_started_at,attempts=1,blocked_until=NULL""",
                (key,_stamp(now)),
            )
            return True
        attempts = row["attempts"] + 1
        blocked = _stamp(now + timedelta(minutes=window_minutes)) if attempts > limit else None
        connection.execute(
            "UPDATE onboarding_rate_limits SET attempts=?,blocked_until=? WHERE rate_key=?",
            (attempts,blocked,key),
        )
        return attempts <= limit

    def _load_application(connection, reference, token):
        row = connection.execute(
            "SELECT * FROM onboarding_applications WHERE reference=?", (reference,)
        ).fetchone()
        if not row or not token or not hmac.compare_digest(row["resume_token_hash"], _token_hash(token)):
            abort(404)
        if row["expires_at"] < _stamp():
            abort(410)
        return row

    @bp.get("/school-network/onboarding")
    def onboarding_page():
        return render_template("school_network/onboarding.html")

    @bp.get("/api/v1/onboarding/applications")
    @login_required
    def list_applications():
        if not platform_owner_check():
            abort(403)
        status = _clean(request.args.get("status"),24)
        connection = connection_factory()
        try:
            if status:
                rows = connection.execute(
                    """SELECT a.reference,a.applicant_email,a.country_code,a.status,a.school_id,
                              a.created_at,a.updated_at,s.name AS school_name,s.lifecycle_status
                       FROM onboarding_applications a LEFT JOIN schools s ON s.id=a.school_id
                       WHERE a.status=? ORDER BY a.updated_at DESC LIMIT 100""",(status,)
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT a.reference,a.applicant_email,a.country_code,a.status,a.school_id,
                              a.created_at,a.updated_at,s.name AS school_name,s.lifecycle_status
                       FROM onboarding_applications a LEFT JOIN schools s ON s.id=a.school_id
                       ORDER BY a.updated_at DESC LIMIT 100"""
                ).fetchall()
            return jsonify({"data":[dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.get("/api/v1/onboarding/countries")
    def countries():
        connection = connection_factory()
        try:
            rows = connection.execute(
                """SELECT country_code,country_name,region_label,region_required,
                          registration_number_required,requirements_json,version
                   FROM country_requirement_profiles WHERE active=1 AND country_code<>'*' ORDER BY country_name"""
            ).fetchall()
            services = connection.execute(
                "SELECT service_key,label,description FROM onboarding_service_catalog WHERE active=1 ORDER BY label"
            ).fetchall()
            return jsonify({"data":{"countries":[dict(row) for row in rows],
                                    "services":[dict(row) for row in services]}})
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/applications")
    def create_draft():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _json_error("invalid_json","A JSON object is required.",400)
        if _clean(payload.get("website"),200):
            return _json_error("accepted","Application received.",202)
        email = _clean(payload.get("contact_email") or payload.get("administrator_email"),254).lower()
        country = _clean(payload.get("country_code"),2).upper()
        if not EMAIL_RE.fullmatch(email) or not re.fullmatch(r"[A-Z]{2}",country):
            return _json_error("validation_error","A valid email and country code are required to save a draft.",422)
        connection = connection_factory()
        try:
            key = _rate_key((request.remote_addr or "unknown") + "|" + email)
            if not _rate_limit(connection,key):
                connection.commit()
                return _json_error("rate_limited","Too many attempts. Please try again later.",429)
            profile = _country_profile(connection,country)
            if not profile:
                return _json_error("country_unavailable","Registration is not currently available for this country.",422)
            reference = "ONB-" + uuid.uuid4().hex[:12].upper()
            token = secrets.token_urlsafe(32)
            verification_token = secrets.token_urlsafe(32)
            expires = _stamp(_now()+timedelta(days=14))
            connection.execute(
                """INSERT INTO onboarding_applications
                   (reference,applicant_email,resume_token_hash,country_code,payload_json,expires_at,verification_token_hash)
                   VALUES (?,?,?,?,?,?,?)""",
                (reference,email,_token_hash(token),country,json.dumps(payload),expires,_token_hash(verification_token)),
            )
            app_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            _record_event(connection,app_id,None,None,"application.draft_created",{"country_code":country})
            verify_url = request.host_url.rstrip("/") + "/api/v1/onboarding/applications/" + reference + "/verify-email"
            _queue_email(connection,email,"Verify your school registration email",
                         "Verify your email using this one-time token: "+verification_token+
                         "\\nVerification endpoint: "+verify_url+
                         "\\nYour resume token is returned once to your browser. Draft expires in 14 days.")
            connection.commit()
            return jsonify({"data":{"reference":reference,"resume_token":token,"status":"draft",
                                    "expires_at":expires,"email_notification_queued":True}}),201
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/applications/<reference>/verify-email")
    def verify_application_email(reference):
        payload = request.get_json(silent=True) or {}
        token = _clean(payload.get("verification_token"), 200)
        connection = connection_factory()
        try:
            row = connection.execute(
                "SELECT * FROM onboarding_applications WHERE reference=?", (reference,)
            ).fetchone()
            if not row or not token or not row["verification_token_hash"] or not hmac.compare_digest(
                row["verification_token_hash"], _token_hash(token)
            ):
                abort(404)
            if row["expires_at"] < _stamp():
                abort(410)
            connection.execute(
                """UPDATE onboarding_applications
                   SET email_verified=1,verification_token_hash=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""", (row["id"],)
            )
            _record_event(connection,row["id"],row["school_id"],None,"application.email_verified",{})
            connection.commit()
            return jsonify({"data":{"reference":reference,"email_verified":True}})
        finally:
            connection.close()

    @bp.get("/api/v1/onboarding/applications/<reference>")
    def get_draft(reference):
        token = request.headers.get("X-Onboarding-Token") or request.args.get("token","")
        connection = connection_factory()
        try:
            row = _load_application(connection,reference,token)
            if row["status"] not in {"draft","submitted","reviewing","approved","activated","configuring","completed","rejected"}:
                abort(404)
            return jsonify({"data":{"reference":reference,"status":row["status"],
                                    "country_code":row["country_code"],"application":json.loads(row["payload_json"]),
                                    "school_id":row["school_id"],"expires_at":row["expires_at"]}})
        finally:
            connection.close()

    @bp.put("/api/v1/onboarding/applications/<reference>")
    def update_draft(reference):
        payload = request.get_json(silent=True)
        token = request.headers.get("X-Onboarding-Token") or (payload or {}).get("resume_token")
        if not isinstance(payload,dict):
            return _json_error("invalid_json","A JSON object is required.",400)
        connection = connection_factory()
        try:
            row = _load_application(connection,reference,token)
            if row["status"] != "draft":
                return _json_error("invalid_state","Only draft applications can be edited.",409)
            merged = json.loads(row["payload_json"])
            merged.update({k:v for k,v in payload.items() if k != "resume_token"})
            email = _clean(merged.get("contact_email") or merged.get("administrator_email"),254).lower()
            country = _clean(merged.get("country_code"),2).upper()
            if not EMAIL_RE.fullmatch(email) or not re.fullmatch(r"[A-Z]{2}",country):
                return _json_error("validation_error","Valid email and country code are required.",422)
            profile = _country_profile(connection,country)
            if not profile:
                return _json_error("country_unavailable","Country is not configured.",422)
            email_changed = email != row["applicant_email"]
            verification_token = secrets.token_urlsafe(32) if email_changed else None
            if email_changed:
                connection.execute(
                    """UPDATE onboarding_applications SET applicant_email=?,country_code=?,payload_json=?,
                       email_verified=0,verification_token_hash=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND status='draft'""",
                    (email,country,json.dumps(merged),_token_hash(verification_token),row["id"]),
                )
                verify_url = request.host_url.rstrip("/") + "/api/v1/onboarding/applications/" + reference + "/verify-email"
                _queue_email(connection,email,"Verify your updated school registration email",
                             "Your application email was changed. Verify the new address using this one-time token: "+
                             verification_token+"\nVerification endpoint: "+verify_url)
            else:
                connection.execute(
                    """UPDATE onboarding_applications SET applicant_email=?,country_code=?,payload_json=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='draft'""",
                    (email,country,json.dumps(merged),row["id"]),
                )
            _record_event(connection,row["id"],None,None,"application.draft_updated",
                          {"country_code":country,"email_changed":email_changed})
            connection.commit()
            return jsonify({"data":{"reference":reference,"status":"draft","updated":True}})
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/applications/<reference>/submit")
    def submit_application(reference):
        payload = request.get_json(silent=True) or {}
        token = request.headers.get("X-Onboarding-Token") or payload.get("resume_token")
        connection = connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _load_application(connection,reference,token)
            if row["status"] != "draft":
                return _json_error("invalid_state","This application has already been submitted.",409)
            if not row["email_verified"]:
                return _json_error("email_not_verified","Verify the applicant email before submitting the application.",403)
            data = json.loads(row["payload_json"])
            profile = _country_profile(connection,row["country_code"])
            services = connection.execute("SELECT service_key,label FROM onboarding_service_catalog WHERE active=1").fetchall()
            checked, errors = _validate_payload(data,profile,services)
            if errors:
                return jsonify({"error":{"code":"validation_error","messages":errors}}),422
            duplicate = connection.execute(
                """SELECT id FROM schools WHERE country_code=? AND
                   (lower(name)=lower(?) OR (?<>'' AND lower(coalesce(registration_number,''))=lower(?))) LIMIT 1""",
                (checked["country_code"],checked["name"],_clean(data.get("registration_number"),100),
                 _clean(data.get("registration_number"),100)),
            ).fetchone()
            if duplicate:
                _record_event(connection,row["id"],None,None,"application.duplicate_flagged",{"match_type":"name_or_registration"})
                connection.commit()
                return _json_error("possible_duplicate","An institution with matching identity details may already exist. Contact platform support for review.",409)
            slug_base = re.sub(r"[^a-z0-9]+","-",checked["name"].lower()).strip("-")[:55] or "institution"
            slug = slug_base
            suffix = 1
            while connection.execute("SELECT 1 FROM schools WHERE slug=?",(slug,)).fetchone():
                suffix += 1
                slug = slug_base[:55-len(str(suffix))] + "-" + str(suffix)
            country_name = profile["country_name"] if profile else checked["country_code"]
            region_label = profile["region_label"] if profile else "Administrative region"
            region = checked["region"]
            city = checked["city"] or "Not provided"
            owner = checked["owner_name"] or checked["contact_email"]
            cursor = connection.execute(
                """INSERT INTO schools
                   (name,slug,status,lifecycle_status,legal_name,institution_type,classification,
                    ownership_type,owner_name,registration_number,website_url,description,
                    address_line1,city,county,region,country_code,contact_email,contact_phone,
                    created_at,updated_at)
                   VALUES (?,?,'suspended','pending',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (checked["name"],slug,checked["legal_name"],data.get("institution_type","independent"),
                 data.get("classification") or "other",data.get("ownership_type") or "other",owner,
                 _clean(data.get("registration_number"),100) or None,_clean(data.get("website_url"),255) or None,
                 _clean(data.get("description"),2000) or None,_clean(data.get("address_line1"),180) or None,
                 city,_clean(data.get("county"),100) or region or None,region or None,checked["country_code"],checked["contact_email"],
                 _clean(data.get("contact_phone"),40) or None),
            )
            school_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO school_contacts(school_id,contact_type,full_name,email,phone,is_primary)
                   VALUES (?,'general',?,?,?,1)""",
                (school_id,owner,checked["contact_email"],_clean(data.get("contact_phone"),40) or None),
            )
            for service in checked["requested_services"]:
                connection.execute("INSERT INTO school_service_requests(school_id,service_key) VALUES (?,?)",(school_id,service))
            connection.execute(
                """UPDATE onboarding_applications SET status='submitted',school_id=?,submitted_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",(school_id,row["id"]),
            )
            _record_event(connection,row["id"],school_id,None,"application.submitted",
                          {"country_code":checked["country_code"],"region_label":region_label,
                           "requested_services":checked["requested_services"]})
            connection.execute(
                """INSERT INTO school_network_audit(actor_user_id,action,target_school_id,details)
                   VALUES (NULL,'school.registration_submitted',?,?)""",
                (school_id,json.dumps({"application_reference":reference,"country_code":checked["country_code"]})),
            )
            _queue_email(connection,row["applicant_email"],"School registration submitted",
                         "Your institution application "+reference+" has been submitted and is awaiting verification. No school workspace is active yet.")
            connection.commit()
            return jsonify({"data":{"reference":reference,"school_id":school_id,"status":"submitted",
                                    "lifecycle_status":"pending","message":"Application submitted for verification."}}),202
        except sqlite3.IntegrityError:
            connection.rollback()
            return _json_error("identity_conflict","The institution identity conflicts with an existing record.",409)
        finally:
            connection.close()

    @bp.put("/api/v1/onboarding/countries/<country_code>")
    @login_required
    def configure_country(country_code):
        if not platform_owner_check():
            abort(403)
        payload = request.get_json(silent=True)
        if not isinstance(payload,dict):
            return _json_error("invalid_json","A JSON object is required.",400)
        code = _clean(country_code,2).upper()
        name = _clean(payload.get("country_name"),100)
        region_label = _clean(payload.get("region_label") or "Administrative region",80)
        if not re.fullmatch(r"[A-Z]{2}",code) or len(name)<2:
            return _json_error("validation_error","Valid country code and name are required.",422)
        requirements = payload.get("requirements",{})
        if not isinstance(requirements,dict) or len(requirements)>30:
            return _json_error("validation_error","Requirements must be a small JSON object.",422)
        if any(not isinstance(k,str) or not (isinstance(v,(str,bool,int)) or (k == 'required_fields' and isinstance(v,list) and all(isinstance(x,str) for x in v))) for k,v in requirements.items()):
            return _json_error("validation_error","Requirement settings must use simple key/value fields.",422)
        connection = connection_factory()
        try:
            connection.execute(
                """INSERT INTO country_requirement_profiles
                   (country_code,country_name,region_label,region_required,registration_number_required,requirements_json,active,version,updated_at)
                   VALUES (?,?,?,?,?,?,1,1,CURRENT_TIMESTAMP)
                   ON CONFLICT(country_code) DO UPDATE SET country_name=excluded.country_name,
                   region_label=excluded.region_label,region_required=excluded.region_required,
                   registration_number_required=excluded.registration_number_required,
                   requirements_json=excluded.requirements_json,active=1,version=country_requirement_profiles.version+1,
                   updated_at=CURRENT_TIMESTAMP""",
                (code,name,region_label,1 if payload.get("region_required") else 0,
                 1 if payload.get("registration_number_required") else 0,json.dumps(requirements,sort_keys=True)),
            )
            _record_event(connection,None,None,int(current_user.id),"country.requirements_updated",
                          {"country_code":code,"region_required":bool(payload.get("region_required")),
                           "registration_number_required":bool(payload.get("registration_number_required"))})
            connection.commit()
            return jsonify({"data":{"country_code":code,"country_name":name,"version":"updated"}})
        finally:
            connection.close()

    @bp.get("/api/v1/onboarding/schools/<int:school_id>/progress")
    @login_required
    def onboarding_progress(school_id):
        is_owner = platform_owner_check()
        if not is_owner and (getattr(current_user,"school_id",None) != school_id or
                             getattr(current_user,"role",None) not in {"school_admin","principal"}):
            abort(404)
        connection = connection_factory()
        try:
            school = connection.execute(
                "SELECT id,name,status,lifecycle_status,country_code FROM schools WHERE id=?",(school_id,)
            ).fetchone()
            if not school:
                abort(404)
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE school_id=? AND role='school_admin' AND status='active'",(school_id,)
            ).fetchone()[0]
            services = connection.execute(
                "SELECT service_key,status FROM school_service_requests WHERE school_id=? ORDER BY service_key",(school_id,)
            ).fetchall()
            application = connection.execute(
                "SELECT reference,status FROM onboarding_applications WHERE school_id=? ORDER BY id DESC LIMIT 1",(school_id,)
            ).fetchone()
            stage = "verification" if school["lifecycle_status"] in {"pending","under_review"} else (
                "approval" if school["lifecycle_status"] == "approved" else (
                    "activation" if school["lifecycle_status"] == "active" else school["lifecycle_status"]
                )
            )
            return jsonify({"data":{"school":dict(school),"stage":stage,
                                    "active_school_administrators":admins,
                                    "requested_services":[dict(row) for row in services],
                                    "application":dict(application) if application else None,
                                    "configuration_complete":bool(application and application["status"]=="completed")}})
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/schools/<int:school_id>/configuration/complete")
    @login_required
    def complete_configuration(school_id):
        if platform_owner_check():
            actor_id = int(current_user.id)
        else:
            if getattr(current_user,"school_id",None) != school_id or getattr(current_user,"role",None) != "school_admin":
                abort(404)
            actor_id = int(current_user.id)
        connection = connection_factory()
        try:
            school = connection.execute(
                "SELECT status,lifecycle_status FROM schools WHERE id=?",(school_id,)
            ).fetchone()
            if not school:
                abort(404)
            if school["status"] != "active" or school["lifecycle_status"] != "active":
                return _json_error("school_not_active","Institution must be activated before configuration can be completed.",409)
            admin = connection.execute(
                "SELECT 1 FROM users WHERE school_id=? AND role='school_admin' AND status='active' LIMIT 1",(school_id,)
            ).fetchone()
            contact = connection.execute(
                "SELECT 1 FROM school_contacts WHERE school_id=? AND status='active' LIMIT 1",(school_id,)
            ).fetchone()
            if not admin or not contact:
                return jsonify({"error":{"code":"configuration_incomplete",
                    "message":"An active school administrator and an active institutional contact are required.",
                    "checks":{"active_administrator":bool(admin),"institutional_contact":bool(contact)}}}),422
            connection.execute(
                "UPDATE onboarding_applications SET status='completed',updated_at=CURRENT_TIMESTAMP WHERE school_id=? AND status<>'completed'",
                (school_id,),
            )
            _record_event(connection,None,school_id,actor_id,"onboarding.configuration_completed",
                          {"active_administrator":True,"institutional_contact":True})
            connection.commit()
            return jsonify({"data":{"school_id":school_id,"status":"completed","configuration_complete":True}})
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/schools/<int:school_id>/invite-administrator")
    @login_required
    def invite_administrator(school_id):
        if not platform_owner_check():
            abort(403)
        payload = request.get_json(silent=True)
        if not isinstance(payload,dict):
            return _json_error("invalid_json","A JSON object is required.",400)
        email = _clean(payload.get("email"),254).lower()
        full_name = _clean(payload.get("full_name"),160)
        if not EMAIL_RE.fullmatch(email) or len(full_name)<2:
            return _json_error("validation_error","Administrator name and valid email are required.",422)
        connection = connection_factory()
        try:
            school = connection.execute("SELECT name,lifecycle_status FROM schools WHERE id=?",(school_id,)).fetchone()
            if not school:
                abort(404)
            if school["lifecycle_status"] != "active":
                return _json_error("school_not_active","Administrator invitations are available only after institution activation.",409)
            existing = connection.execute(
                "SELECT id FROM users WHERE lower(username)=lower(?)",(email,)
            ).fetchone()
            if existing:
                return _json_error("account_exists","An account with this email/username already exists. Use the existing account invitation workflow.",409)
            pending = connection.execute(
                """SELECT 1 FROM school_admin_invitations
                   WHERE school_id=? AND lower(email)=lower(?) AND accepted_at IS NULL AND expires_at>? LIMIT 1""",
                (school_id,email,_stamp()),
            ).fetchone()
            if pending:
                return _json_error("invitation_exists","A valid invitation already exists for this administrator.",409)
            token = secrets.token_urlsafe(40)
            expires = _stamp(_now()+timedelta(hours=72))
            connection.execute(
                """INSERT INTO school_admin_invitations(school_id,email,full_name,token_hash,expires_at,invited_by)
                   VALUES (?,?,?,?,?,?)""",
                (school_id,email,full_name,_token_hash(token),expires,int(current_user.id)),
            )
            accept_url = request.host_url.rstrip("/") + "/api/v1/onboarding/invitations/accept"
            _queue_email(connection,email,"Invitation to administer "+school["name"],
                         "You have been invited to administer "+school["name"]+".\nInvitation token: "+token+
                         "\nAccept endpoint: "+accept_url+"\nThis invitation expires in 72 hours.")
            _record_event(connection,None,school_id,int(current_user.id),"administrator.invitation_issued",
                          {"email":email,"expires_at":expires})
            connection.commit()
            return jsonify({"data":{"school_id":school_id,"email":email,"expires_at":expires,
                                    "notification_queued":True}}),202
        except sqlite3.IntegrityError:
            connection.rollback()
            return _json_error("invitation_exists","A pending invitation already exists for this administrator.",409)
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/invitations/accept")
    def accept_invitation():
        payload = request.get_json(silent=True)
        if not isinstance(payload,dict):
            return _json_error("invalid_json","A JSON object is required.",400)
        token = _clean(payload.get("token"),200)
        password = str(payload.get("password") or "")
        if len(password)<12 or len(password)>256:
            return _json_error("weak_password","Use a password between 12 and 256 characters.",422)
        connection = connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            invitation = connection.execute(
                "SELECT * FROM school_admin_invitations WHERE token_hash=? AND accepted_at IS NULL",
                (_token_hash(token),),
            ).fetchone()
            if not invitation or invitation["expires_at"] < _stamp():
                return _json_error("invalid_invitation","Invitation is invalid or expired.",404)
            school = connection.execute("SELECT lifecycle_status,status FROM schools WHERE id=?",(invitation["school_id"],)).fetchone()
            if not school or school["lifecycle_status"] not in {"approved","active"}:
                return _json_error("school_not_approved","Institution is not approved for administrator onboarding.",409)
            if connection.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)",(invitation["email"],)).fetchone():
                return _json_error("account_exists","An account already exists for this email.",409)
            if school["lifecycle_status"] != "active" or school["status"] != "active":
                return _json_error("school_not_active","The institution must be active before an administrator account can be created.",409)
            status = "active"
            cursor = connection.execute(
                """INSERT INTO users(username,full_name,password_hash,role,status,is_super_admin,school_id)
                   VALUES (?,?,?,'school_admin',?,0,?)""",
                (invitation["email"],invitation["full_name"],generate_password_hash(password),status,invitation["school_id"]),
            )
            connection.execute(
                "UPDATE school_admin_invitations SET accepted_at=CURRENT_TIMESTAMP,accepted_user_id=? WHERE id=?",
                (cursor.lastrowid,invitation["id"]),
            )
            _record_event(connection,None,invitation["school_id"],None,"administrator.invitation_accepted",
                          {"user_id":cursor.lastrowid,"email":invitation["email"]})
            _queue_email(connection,invitation["email"],"Administrator account created",
                         "Your administrator account has been created. Account status: "+status+".")
            connection.commit()
            return jsonify({"data":{"user_id":cursor.lastrowid,"school_id":invitation["school_id"],
                                    "status":status,"message":"Invitation accepted."}}),201
        except sqlite3.IntegrityError:
            connection.rollback()
            return _json_error("account_exists","An account already exists for this email.",409)
        finally:
            connection.close()

    @bp.post("/api/v1/onboarding/notifications/dispatch")
    @login_required
    def dispatch_notifications():
        if not platform_owner_check():
            abort(403)
        host = os.environ.get("SMTP_HOST")
        if not host:
            return _json_error("email_not_configured","SMTP is not configured; notifications remain safely queued.",503)
        port = int(os.environ.get("SMTP_PORT","587"))
        username = os.environ.get("SMTP_USERNAME")
        password = os.environ.get("SMTP_PASSWORD")
        sender = os.environ.get("SMTP_FROM") or username
        if not sender:
            return _json_error("email_not_configured","SMTP_FROM or SMTP_USERNAME is required.",503)
        connection = connection_factory()
        sent = 0
        try:
            rows = connection.execute(
                "SELECT * FROM onboarding_notification_outbox WHERE status IN ('pending','failed') AND attempts<5 ORDER BY id LIMIT 25"
            ).fetchall()
            for row in rows:
                message = EmailMessage()
                message["From"] = sender
                message["To"] = row["recipient"]
                message["Subject"] = row["subject"]
                message.set_content(row["body"])
                try:
                    with smtplib.SMTP(host,port,timeout=15) as smtp:
                        smtp.starttls()
                        if username and password:
                            smtp.login(username,password)
                        smtp.send_message(message)
                    connection.execute(
                        "UPDATE onboarding_notification_outbox SET status='sent',attempts=attempts+1,sent_at=CURRENT_TIMESTAMP,last_error=NULL WHERE id=?",
                        (row["id"],),
                    )
                    sent += 1
                except Exception as exc:
                    connection.execute(
                        "UPDATE onboarding_notification_outbox SET status='failed',attempts=attempts+1,last_error=? WHERE id=?",
                        (str(exc)[:500],row["id"]),
                    )
            connection.commit()
            return jsonify({"data":{"sent":sent,"processed":len(rows)}})
        finally:
            connection.close()

    return bp
