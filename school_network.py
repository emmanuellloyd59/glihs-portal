"""School Network foundation for the existing Flask application.

This module deliberately uses the application's existing SQLite connection
factory and Flask-Login identity. It adds tenant metadata and workflows without
replacing legacy school IDs or changing existing student/account records.
"""
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required


CLASSIFICATIONS = {
    "early_childhood", "primary", "secondary", "combined",
    "vocational", "technical", "tertiary", "special_education", "other",
}
OWNERSHIP_TYPES = {
    "government", "private", "faith_based", "community", "nonprofit", "public_private", "other",
}
INSTITUTION_TYPES = {"independent", "group_affiliated"}
SCHOOL_STATUSES = {"pending", "under_review", "approved", "active", "suspended", "rejected", "archived"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SLUGS = {"admin", "api", "login", "logout", "register", "platform", "www", "school", "schools"}


def _columns(connection, table):
    return {row["name"] for row in connection.execute("PRAGMA table_info(" + table + ")")}


def initialize_school_network_schema(connection_factory):
    """Apply idempotent additive SQLite migrations; preserve all existing rows."""
    connection = connection_factory()
    try:
        connection.execute("BEGIN IMMEDIATE")
        school_columns = _columns(connection, "schools")
        additions = {
            "lifecycle_status": "TEXT NOT NULL DEFAULT 'active'",
            "legal_name": "TEXT",
            "institution_type": "TEXT NOT NULL DEFAULT 'independent'",
            "classification": "TEXT",
            "ownership_type": "TEXT",
            "owner_name": "TEXT",
            "registration_number": "TEXT",
            "website_url": "TEXT",
            "description": "TEXT",
            "address_line1": "TEXT",
            "address_line2": "TEXT",
            "city": "TEXT",
            "county": "TEXT",
            "region": "TEXT",
            "postal_code": "TEXT",
            "country_code": "TEXT",
            "timezone": "TEXT",
            "contact_email": "TEXT",
            "contact_phone": "TEXT",
            "updated_at": "TEXT",
        }
        for column, declaration in additions.items():
            if column not in school_columns:
                connection.execute("ALTER TABLE schools ADD COLUMN " + column + " " + declaration)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS school_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                legal_name TEXT,
                ownership_type TEXT,
                owner_name TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended', 'archived')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS campuses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL REFERENCES schools(id),
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                campus_type TEXT NOT NULL DEFAULT 'standard',
                address_line1 TEXT,
                address_line2 TEXT,
                city TEXT,
                county TEXT,
                region TEXT,
                postal_code TEXT,
                country_code TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                is_main INTEGER NOT NULL DEFAULT 0 CHECK (is_main IN (0, 1)),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended', 'archived')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                UNIQUE (school_id, code),
                UNIQUE (school_id, id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS school_group_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES school_groups(id),
                school_id INTEGER NOT NULL REFERENCES schools(id),
                relationship_type TEXT NOT NULL DEFAULT 'member'
                    CHECK (relationship_type IN ('headquarters', 'member', 'affiliate')),
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (group_id, school_id),
                UNIQUE (school_id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS school_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id INTEGER NOT NULL REFERENCES schools(id),
                campus_id INTEGER,
                contact_type TEXT NOT NULL
                    CHECK (contact_type IN ('general', 'admissions', 'principal', 'owner', 'finance', 'technical', 'other')),
                full_name TEXT,
                job_title TEXT,
                email TEXT,
                phone TEXT,
                is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'inactive')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (school_id, campus_id) REFERENCES campuses(school_id, id)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS school_network_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                target_school_id INTEGER,
                target_group_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_schools_lifecycle ON schools(lifecycle_status, created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_campuses_school_status ON campuses(school_id, status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_school_contacts_school ON school_contacts(school_id, status)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_school_group_memberships_group ON school_group_memberships(group_id, school_id)")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_campuses_one_main ON campuses(school_id) WHERE is_main=1")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _is_platform_owner():
    return (
        current_user.is_authenticated
        and getattr(current_user, "role", None) == "super_admin"
        and bool(getattr(current_user, "is_super_admin", False))
        and getattr(current_user, "school_id", None) is None
    )


def _school_access(school_id, connection_factory, permission="school.manage_profile"):
    """Authorize by scoped RBAC/ABAC and re-check the tenant lifecycle."""
    if _is_platform_owner():
        return "platform"
    if not current_user.is_authenticated:
        abort(401)
    from authorization import require_permission
    require_permission(permission, connection_factory, school_id=school_id)
    connection = connection_factory()
    try:
        school = connection.execute(
            "SELECT status, lifecycle_status FROM schools WHERE id=?", (school_id,)
        ).fetchone()
    finally:
        connection.close()
    if school is None or school["status"] != "active" or school["lifecycle_status"] != "active":
        abort(403)
    return "school"


def _clean(value, limit=255):
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _slugify(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:63].strip("-")


def _validate_school(payload, require_registration=True):
    data = {}
    data["name"] = _clean(payload.get("name"), 160)
    data["legal_name"] = _clean(payload.get("legal_name") or data["name"], 200)
    data["slug"] = _clean(payload.get("slug") or _slugify(data["name"]), 63).lower()
    data["institution_type"] = _clean(payload.get("institution_type") or "independent", 32)
    data["classification"] = _clean(payload.get("classification"), 40)
    data["ownership_type"] = _clean(payload.get("ownership_type"), 40)
    data["owner_name"] = _clean(payload.get("owner_name"), 160)
    data["registration_number"] = _clean(payload.get("registration_number"), 100)
    data["website_url"] = _clean(payload.get("website_url"), 255)
    data["description"] = _clean(payload.get("description"), 2000)
    data["address_line1"] = _clean(payload.get("address_line1"), 180)
    data["address_line2"] = _clean(payload.get("address_line2"), 180)
    data["city"] = _clean(payload.get("city"), 100)
    data["county"] = _clean(payload.get("county"), 100)
    data["region"] = _clean(payload.get("region"), 100)
    data["postal_code"] = _clean(payload.get("postal_code"), 24)
    data["country_code"] = _clean(payload.get("country_code"), 2).upper()
    data["timezone"] = _clean(payload.get("timezone"), 64)
    data["contact_email"] = _clean(payload.get("contact_email"), 254).lower()
    data["contact_phone"] = _clean(payload.get("contact_phone"), 40)
    errors = []
    if (require_registration or "name" in payload) and len(data["name"]) < 2:
        errors.append("Institution name must contain at least 2 characters.")
    if not SLUG_RE.fullmatch(data["slug"]) or data["slug"] in RESERVED_SLUGS:
        errors.append("Choose a valid, non-reserved school identity (lowercase letters, numbers and hyphens).")
    if (require_registration or data["institution_type"]) and data["institution_type"] not in INSTITUTION_TYPES:
        errors.append("Choose a valid institution type.")
    if (require_registration or data["classification"]) and data["classification"] not in CLASSIFICATIONS:
        errors.append("Choose a valid institutional classification.")
    if (require_registration or data["ownership_type"]) and data["ownership_type"] not in OWNERSHIP_TYPES:
        errors.append("Choose a valid ownership classification.")
    if require_registration and not data["owner_name"]:
        errors.append("Ownership information must include the responsible owner or authority.")
    if require_registration and (not data["city"] or not data["country_code"] or len(data["country_code"]) != 2):
        errors.append("City and a two-letter country code are required.")
    if require_registration and not data["contact_email"] and not data["contact_phone"]:
        errors.append("Provide at least one institutional contact method (email or phone).")
    if data["country_code"] and not re.fullmatch(r"[A-Z]{2}", data["country_code"]):
        errors.append("Country code must contain exactly two letters.")
    if data["contact_email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", data["contact_email"]):
        errors.append("Enter a valid institutional email address.")
    if data["website_url"] and not re.fullmatch(r"https?://[^\s]+", data["website_url"], re.I):
        errors.append("Website URL must begin with http:// or https://.")
    return data, errors


def _school_dict(row):
    return {key: row[key] for key in row.keys()}


def _audit(connection, action, school_id=None, group_id=None, details=None):
    connection.execute(
        """INSERT INTO school_network_audit
           (actor_user_id, action, target_school_id, target_group_id, details)
           VALUES (?, ?, ?, ?, ?)""",
        (
            int(current_user.id) if current_user.is_authenticated else None,
            action, school_id, group_id,
            json.dumps(details or {}, sort_keys=True)[:4000],
        ),
    )


def create_school_network_blueprint(connection_factory):
    bp = Blueprint("school_network", __name__)

    @bp.before_request
    def require_api_authentication():
        if (request.path.startswith("/api/v1/") and not current_user.is_authenticated
                and not (request.path == "/api/v1/schools" and request.method == "POST")):
            abort(401)

    @bp.app_context_processor
    def inject_institution_brand():
        school_id = getattr(current_user, "school_id", None) if current_user.is_authenticated else None
        if current_user.is_authenticated and _is_platform_owner():
            selected = session.get("active_school_id")
            if isinstance(selected, int) and not isinstance(selected, bool):
                school_id = selected
        name = None
        if school_id is not None:
            connection = connection_factory()
            try:
                row = connection.execute(
                    "SELECT name FROM schools WHERE id=? AND status='active'", (school_id,)
                ).fetchone()
                name = row["name"] if row else None
            finally:
                connection.close()
        return {"current_institution_name": name, "platform_name": "Digital Education Ecosystem"}

    def _registration_context(form_data=None):
        connection = connection_factory()
        try:
            groups = connection.execute(
                "SELECT id, name FROM school_groups WHERE status='active' ORDER BY name COLLATE NOCASE"
            ).fetchall()
        finally:
            connection.close()
        return {"classifications": sorted(CLASSIFICATIONS), "ownership_types": sorted(OWNERSHIP_TYPES),
                "groups": groups, "form_data": form_data}

    @bp.get("/school-network/register")
    def register_form():
        return render_template("school_network/register.html", **_registration_context())

    @bp.post("/school-network/register")
    def register_school_form():
        data, errors = _validate_school(request.form)
        group_id = _clean(request.form.get("group_id"), 16)
        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("school_network/register.html", **_registration_context(request.form)), 422
        connection = connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM schools WHERE slug=?", (data["slug"],)).fetchone():
                errors.append("That school identity is already registered. Choose another identity.")
            if data["institution_type"] == "group_affiliated":
                if not group_id.isdigit() or not connection.execute(
                    "SELECT 1 FROM school_groups WHERE id=? AND status='active'", (int(group_id),)
                ).fetchone():
                    errors.append("Select a valid active school group for this institution.")
            if errors:
                connection.rollback()
                for error in errors:
                    flash(error, "danger")
                return render_template("school_network/register.html", **_registration_context(request.form)), 422
            cursor = connection.execute(
                """INSERT INTO schools
                   (name, slug, status, lifecycle_status, legal_name, institution_type,
                    classification, ownership_type, owner_name, registration_number,
                    website_url, description, address_line1, address_line2, city, county,
                    region, postal_code, country_code, timezone, contact_email, contact_phone,
                    created_at, updated_at)
                   VALUES (?, ?, 'suspended', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                tuple(data[key] for key in (
                    "name", "slug", "legal_name", "institution_type", "classification",
                    "ownership_type", "owner_name", "registration_number", "website_url",
                    "description", "address_line1", "address_line2", "city", "county",
                    "region", "postal_code", "country_code", "timezone", "contact_email", "contact_phone"
                )),
            )
            school_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO school_contacts
                   (school_id, contact_type, full_name, email, phone, is_primary)
                   VALUES (?, 'general', ?, ?, ?, 1)""",
                (school_id, data["owner_name"], data["contact_email"] or None, data["contact_phone"] or None),
            )
            if data["institution_type"] == "group_affiliated":
                connection.execute(
                    """INSERT INTO school_group_memberships (group_id, school_id, relationship_type)
                       VALUES (?, ?, 'member')""", (int(group_id), school_id)
                )
            _audit(connection, "school.registration_submitted", school_id,
                   details={"slug": data["slug"], "institution_type": data["institution_type"]})
            connection.commit()
            flash("Institution registration submitted. It remains inactive until platform review and activation.", "success")
            return redirect(url_for("school_network.register_form", submitted="1"))
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("The school identity or group relationship already exists. Review the details and try again.", "danger")
            return render_template("school_network/register.html", classifications=sorted(CLASSIFICATIONS),
                                   ownership_types=sorted(OWNERSHIP_TYPES), form_data=request.form), 409
        finally:
            connection.close()

    @bp.post("/api/v1/schools")
    def api_register_school():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        data, errors = _validate_school(payload)
        group_id = payload.get("group_id")
        if errors:
            return jsonify({"error": {"code": "validation_error", "messages": errors}}), 422
        connection = connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM schools WHERE slug=?", (data["slug"],)).fetchone():
                return jsonify({"error": {"code": "identity_conflict", "message": "That school identity is already registered."}}), 409
            if data["institution_type"] == "group_affiliated":
                if not isinstance(group_id, int) or not connection.execute(
                    "SELECT 1 FROM school_groups WHERE id=? AND status='active'", (group_id,)
                ).fetchone():
                    return jsonify({"error": {"code": "invalid_group", "message": "A valid active school group is required."}}), 422
            cursor = connection.execute(
                """INSERT INTO schools
                   (name, slug, status, lifecycle_status, legal_name, institution_type,
                    classification, ownership_type, owner_name, registration_number,
                    website_url, description, address_line1, address_line2, city, county,
                    region, postal_code, country_code, timezone, contact_email, contact_phone,
                    created_at, updated_at)
                   VALUES (?, ?, 'suspended', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                tuple(data[key] for key in (
                    "name", "slug", "legal_name", "institution_type", "classification",
                    "ownership_type", "owner_name", "registration_number", "website_url",
                    "description", "address_line1", "address_line2", "city", "county",
                    "region", "postal_code", "country_code", "timezone", "contact_email", "contact_phone"
                )),
            )
            school_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO school_contacts
                   (school_id, contact_type, full_name, email, phone, is_primary)
                   VALUES (?, 'general', ?, ?, ?, 1)""",
                (school_id, data["owner_name"], data["contact_email"] or None, data["contact_phone"] or None),
            )
            if data["institution_type"] == "group_affiliated":
                connection.execute(
                    "INSERT INTO school_group_memberships (group_id, school_id) VALUES (?, ?)",
                    (group_id, school_id)
                )
            _audit(connection, "school.registration_submitted", school_id,
                   details={"slug": data["slug"], "institution_type": data["institution_type"]})
            connection.commit()
            return jsonify({"data": {"id": school_id, "slug": data["slug"], "lifecycle_status": "pending"}}), 202
        except sqlite3.IntegrityError:
            connection.rollback()
            return jsonify({"error": {"code": "identity_conflict", "message": "The school identity or group relationship already exists."}}), 409
        finally:
            connection.close()

    @bp.get("/api/v1/school-groups")
    @login_required
    def api_list_groups():
        if not _is_platform_owner():
            abort(403)
        connection = connection_factory()
        try:
            rows = connection.execute("SELECT * FROM school_groups ORDER BY name COLLATE NOCASE").fetchall()
            return jsonify({"data": [_school_dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.post("/platform/groups")
    @login_required
    def platform_create_group_form():
        if not _is_platform_owner():
            abort(403)
        name = _clean(request.form.get("group_name"), 160)
        slug = _clean(request.form.get("group_slug") or _slugify(name), 63).lower()
        owner = _clean(request.form.get("group_owner"), 160)
        if len(name) < 2 or not SLUG_RE.fullmatch(slug) or slug in RESERVED_SLUGS:
            flash("Enter a valid group name and unique identity.", "danger")
            return redirect(url_for("school_network.platform_schools"))
        connection = connection_factory()
        try:
            cursor = connection.execute(
                "INSERT INTO school_groups (name, slug, legal_name, owner_name) VALUES (?, ?, ?, ?)",
                (name, slug, name, owner or None),
            )
            _audit(connection, "school_group.created", group_id=cursor.lastrowid, details={"slug": slug})
            connection.commit()
            flash("School group created. Institutions can now register as group members.", "success")
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("That group identity is already in use.", "danger")
        finally:
            connection.close()
        return redirect(url_for("school_network.platform_schools"))

    @bp.get("/api/v1/schools")
    @login_required
    def api_list_schools():
        connection = connection_factory()
        try:
            if _is_platform_owner():
                rows = connection.execute("SELECT * FROM schools ORDER BY created_at DESC, id DESC").fetchall()
            elif current_user.school_id is not None and current_user.role in {"school_admin", "principal"}:
                rows = connection.execute(
                    "SELECT * FROM schools WHERE id=? AND status='active' AND lifecycle_status='active'",
                    (current_user.school_id,),
                ).fetchall()
                if not rows:
                    abort(403)
            else:
                abort(403)
            return jsonify({"data": [_school_dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.get("/api/v1/schools/<int:school_id>")
    @login_required
    def api_get_school(school_id):
        _school_access(school_id, connection_factory, "school.view_dashboard")
        connection = connection_factory()
        try:
            row = connection.execute("SELECT * FROM schools WHERE id=?", (school_id,)).fetchone()
            if row is None:
                abort(404)
            campuses = connection.execute(
                "SELECT * FROM campuses WHERE school_id=? ORDER BY is_main DESC, name", (school_id,)
            ).fetchall()
            return jsonify({"data": _school_dict(row), "campuses": [_school_dict(item) for item in campuses]})
        finally:
            connection.close()

    @bp.patch("/api/v1/schools/<int:school_id>")
    @login_required
    def api_update_school(school_id):
        access = _school_access(school_id, connection_factory, "school.manage_profile")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        connection = connection_factory()
        try:
            row = connection.execute("SELECT * FROM schools WHERE id=?", (school_id,)).fetchone()
            if row is None:
                abort(404)
            allowed = {
                "name", "legal_name", "classification", "ownership_type", "owner_name",
                "registration_number", "website_url", "description", "address_line1",
                "address_line2", "city", "county", "region", "postal_code",
                "country_code", "timezone", "contact_email", "contact_phone",
            }
            if access == "platform":
                allowed.add("institution_type")
            changes = {key: payload[key] for key in allowed if key in payload}
            if not changes:
                return jsonify({"error": {"code": "no_changes", "message": "No editable fields were supplied."}}), 400
            merged = {key: row[key] for key in row.keys()}
            merged.update(changes)
            checked, errors = _validate_school(merged, require_registration=False)
            if errors:
                return jsonify({"error": {"code": "validation_error", "messages": errors}}), 422
            assignments = ", ".join(key + "=?" for key in changes)
            values = [checked[key] for key in changes]
            connection.execute(
                "UPDATE schools SET " + assignments + ", updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*values, school_id),
            )
            _audit(connection, "school.profile_updated", school_id, details={"fields": sorted(changes)})
            connection.commit()
            updated = connection.execute("SELECT * FROM schools WHERE id=?", (school_id,)).fetchone()
            return jsonify({"data": _school_dict(updated)})
        finally:
            connection.close()

    @bp.post("/api/v1/schools/<int:school_id>/review")
    @login_required
    def api_review_school(school_id):
        if not _is_platform_owner():
            abort(403)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        action = _clean(payload.get("action"), 24).lower()
        transitions = {
            "start_review": ({"pending"}, "under_review", "suspended"),
            "approve": ({"pending", "under_review"}, "approved", "suspended"),
            "reject": ({"pending", "under_review"}, "rejected", "suspended"),
            "activate": ({"approved"}, "active", "active"),
            "suspend": ({"active"}, "suspended", "suspended"),
            "reactivate": ({"suspended"}, "active", "active"),
            "archive": ({"rejected", "suspended"}, "archived", "suspended"),
        }
        if action not in transitions:
            return jsonify({"error": {"code": "invalid_action", "message": "Unsupported institution lifecycle action."}}), 422
        connection = connection_factory()
        try:
            row = connection.execute("SELECT lifecycle_status FROM schools WHERE id=?", (school_id,)).fetchone()
            if row is None:
                abort(404)
            allowed_from, lifecycle, legacy_status = transitions[action]
            if row["lifecycle_status"] not in allowed_from:
                return jsonify({"error": {"code": "invalid_transition", "message": "That lifecycle transition is not allowed."}}), 409
            connection.execute(
                """UPDATE schools SET lifecycle_status=?, status=?, updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""", (lifecycle, legacy_status, school_id)
            )
            _audit(connection, "school." + action, school_id, details={"from": row["lifecycle_status"], "to": lifecycle})
            connection.commit()
            updated = connection.execute("SELECT id, name, slug, status, lifecycle_status FROM schools WHERE id=?", (school_id,)).fetchone()
            return jsonify({"data": _school_dict(updated)})
        finally:
            connection.close()

    @bp.post("/api/v1/school-groups")
    @login_required
    def api_create_group():
        if not _is_platform_owner():
            abort(403)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        name = _clean(payload.get("name"), 160)
        slug = _clean(payload.get("slug") or _slugify(name), 63).lower()
        ownership = _clean(payload.get("ownership_type"), 40)
        owner = _clean(payload.get("owner_name"), 160)
        if len(name) < 2 or not SLUG_RE.fullmatch(slug) or slug in RESERVED_SLUGS:
            return jsonify({"error": {"code": "validation_error", "message": "Provide a valid group name and unique identity."}}), 422
        if ownership and ownership not in OWNERSHIP_TYPES:
            return jsonify({"error": {"code": "validation_error", "message": "Invalid group ownership classification."}}), 422
        connection = connection_factory()
        try:
            cursor = connection.execute(
                """INSERT INTO school_groups (name, slug, legal_name, ownership_type, owner_name, contact_email, contact_phone)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, slug, _clean(payload.get("legal_name"), 200) or name, ownership or None,
                 owner or None, _clean(payload.get("contact_email"), 254).lower() or None,
                 _clean(payload.get("contact_phone"), 40) or None),
            )
            group_id = cursor.lastrowid
            _audit(connection, "school_group.created", group_id=group_id, details={"slug": slug})
            connection.commit()
            return jsonify({"data": {"id": group_id, "name": name, "slug": slug, "status": "active"}}), 201
        except sqlite3.IntegrityError:
            connection.rollback()
            return jsonify({"error": {"code": "identity_conflict", "message": "That group identity is already in use."}}), 409
        finally:
            connection.close()

    @bp.get("/api/v1/schools/<int:school_id>/campuses")
    @login_required
    def api_list_campuses(school_id):
        _school_access(school_id, connection_factory, "school.view_dashboard")
        connection = connection_factory()
        try:
            rows = connection.execute("SELECT * FROM campuses WHERE school_id=? ORDER BY is_main DESC, name", (school_id,)).fetchall()
            return jsonify({"data": [_school_dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.post("/api/v1/schools/<int:school_id>/campuses")
    @login_required
    def api_create_campus(school_id):
        access = _school_access(school_id, connection_factory, "school.manage_campuses")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        code = _clean(payload.get("code"), 32).upper()
        name = _clean(payload.get("name"), 160)
        campus_type = _clean(payload.get("campus_type") or "standard", 40)
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code) or len(name) < 2:
            return jsonify({"error": {"code": "validation_error", "message": "Campus code and name are required and must be valid."}}), 422
        connection = connection_factory()
        try:
            school = connection.execute("SELECT lifecycle_status FROM schools WHERE id=?", (school_id,)).fetchone()
            if school is None:
                abort(404)
            if school["lifecycle_status"] != "active" and access != "platform":
                abort(403)
            cursor = connection.execute(
                """INSERT INTO campuses
                   (school_id, code, name, campus_type, address_line1, city, county,
                    country_code, contact_email, contact_phone, is_main)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (school_id, code, name, campus_type, _clean(payload.get("address_line1"), 180) or None,
                 _clean(payload.get("city"), 100) or None, _clean(payload.get("county"), 100) or None,
                 _clean(payload.get("country_code"), 2).upper() or None,
                 _clean(payload.get("contact_email"), 254).lower() or None,
                 _clean(payload.get("contact_phone"), 40) or None,
                 1 if payload.get("is_main") is True else 0),
            )
            campus_id = cursor.lastrowid
            _audit(connection, "campus.created", school_id, details={"campus_id": campus_id, "code": code})
            connection.commit()
            return jsonify({"data": {"id": campus_id, "school_id": school_id, "code": code, "name": name, "status": "active"}}), 201
        except sqlite3.IntegrityError:
            connection.rollback()
            return jsonify({"error": {"code": "campus_conflict", "message": "That campus code already exists for this school."}}), 409
        finally:
            connection.close()

    @bp.patch("/api/v1/schools/<int:school_id>/campuses/<int:campus_id>")
    @login_required
    def api_update_campus(school_id, campus_id):
        access = _school_access(school_id, connection_factory, "school.manage_campuses")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        allowed = {"name", "campus_type", "address_line1", "city", "county",
                   "country_code", "contact_email", "contact_phone", "status"}
        changes = {key: payload[key] for key in allowed if key in payload}
        if not changes:
            return jsonify({"error": {"code": "no_changes", "message": "No campus fields were supplied."}}), 400
        if "status" in changes and changes["status"] not in {"active", "suspended", "archived"}:
            return jsonify({"error": {"code": "validation_error", "message": "Invalid campus status."}}), 422
        if "name" in changes and len(_clean(changes["name"], 160)) < 2:
            return jsonify({"error": {"code": "validation_error", "message": "Campus name is required."}}), 422
        connection = connection_factory()
        try:
            campus = connection.execute(
                "SELECT id FROM campuses WHERE id=? AND school_id=?", (campus_id, school_id)
            ).fetchone()
            if campus is None:
                abort(404)
            if access != "platform":
                lifecycle = connection.execute("SELECT lifecycle_status FROM schools WHERE id=?", (school_id,)).fetchone()
                if lifecycle["lifecycle_status"] != "active":
                    abort(403)
            values = []
            assignments = []
            for key, value in changes.items():
                if key == "contact_email":
                    value = _clean(value, 254).lower() or None
                elif key == "country_code":
                    value = _clean(value, 2).upper() or None
                elif key in {"name", "campus_type", "address_line1", "city", "county", "contact_phone"}:
                    value = _clean(value, 180) or None
                assignments.append(key + "=?")
                values.append(value)
            connection.execute(
                "UPDATE campuses SET " + ", ".join(assignments) + ", updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?",
                (*values, campus_id, school_id),
            )
            _audit(connection, "campus.updated", school_id, details={"campus_id": campus_id, "fields": sorted(changes)})
            connection.commit()
            return jsonify({"data": {"id": campus_id, "school_id": school_id, "updated": sorted(changes)}})
        finally:
            connection.close()

    @bp.post("/api/v1/schools/<int:school_id>/contacts")
    @login_required
    def api_create_contact(school_id):
        access = _school_access(school_id, connection_factory, "school.manage_profile")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "A JSON object is required."}}), 400
        contact_type = _clean(payload.get("contact_type") or "general", 24)
        if contact_type not in {"general", "admissions", "principal", "owner", "finance", "technical", "other"}:
            return jsonify({"error": {"code": "validation_error", "message": "Invalid contact type."}}), 422
        campus_id = payload.get("campus_id")
        connection = connection_factory()
        try:
            if connection.execute("SELECT 1 FROM schools WHERE id=?", (school_id,)).fetchone() is None:
                abort(404)
            if campus_id is not None:
                if not isinstance(campus_id, int) or not connection.execute(
                    "SELECT 1 FROM campuses WHERE id=? AND school_id=?", (campus_id, school_id)
                ).fetchone():
                    return jsonify({"error": {"code": "invalid_campus", "message": "Campus does not belong to this school."}}), 422
            if access != "platform":
                lifecycle = connection.execute("SELECT lifecycle_status FROM schools WHERE id=?", (school_id,)).fetchone()
                if lifecycle["lifecycle_status"] != "active":
                    abort(403)
            cursor = connection.execute(
                """INSERT INTO school_contacts
                   (school_id, campus_id, contact_type, full_name, job_title, email, phone, is_primary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (school_id, campus_id, contact_type, _clean(payload.get("full_name"), 160) or None,
                 _clean(payload.get("job_title"), 100) or None, _clean(payload.get("email"), 254).lower() or None,
                 _clean(payload.get("phone"), 40) or None, 1 if payload.get("is_primary") is True else 0),
            )
            _audit(connection, "school_contact.created", school_id, details={"contact_id": cursor.lastrowid})
            connection.commit()
            return jsonify({"data": {"id": cursor.lastrowid, "school_id": school_id, "campus_id": campus_id}}), 201
        finally:
            connection.close()

    @bp.get("/platform/schools")
    @login_required
    def platform_schools():
        if not _is_platform_owner():
            abort(403)
        connection = connection_factory()
        try:
            rows = connection.execute(
                """SELECT s.*, g.name AS group_name
                   FROM schools s
                   LEFT JOIN school_group_memberships gm ON gm.school_id=s.id
                   LEFT JOIN school_groups g ON g.id=gm.group_id
                   ORDER BY CASE s.lifecycle_status WHEN 'pending' THEN 0 WHEN 'under_review' THEN 1 ELSE 2 END,
                            s.created_at DESC, s.id DESC"""
            ).fetchall()
            return render_template("school_network/platform_schools.html", schools=rows,
                                   statuses=sorted(SCHOOL_STATUSES))
        finally:
            connection.close()

    @bp.route("/school-network/profile", methods=["GET", "POST"])
    @login_required
    def school_profile():
        if _is_platform_owner():
            selected = request.args.get("school_id", type=int)
            if not selected:
                abort(400)
            school_id = selected
            access = "platform"
        else:
            school_id = getattr(current_user, "school_id", None)
            if school_id is None or current_user.role != "school_admin":
                abort(403)
            access = _school_access(school_id, connection_factory)
        connection = connection_factory()
        try:
            school = connection.execute("SELECT * FROM schools WHERE id=?", (school_id,)).fetchone()
            if school is None:
                abort(404)
            if request.method == "POST":
                action = _clean(request.form.get("action"), 32)
                if action == "create_campus":
                    if access != "platform" and school["lifecycle_status"] != "active":
                        abort(403)
                    code = _clean(request.form.get("campus_code"), 32).upper()
                    campus_name = _clean(request.form.get("campus_name"), 160)
                    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code) or len(campus_name) < 2:
                        flash("Enter a valid campus name and code.", "danger")
                    else:
                        try:
                            cursor = connection.execute(
                                """INSERT INTO campuses (school_id, code, name, campus_type, city, county, country_code, is_main)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (school_id, code, campus_name, _clean(request.form.get("campus_type") or "standard", 40),
                                 _clean(request.form.get("campus_city"), 100) or None,
                                 _clean(request.form.get("campus_county"), 100) or None,
                                 _clean(request.form.get("campus_country_code"), 2).upper() or None,
                                 1 if request.form.get("is_main") == "yes" else 0),
                            )
                            _audit(connection, "campus.created", school_id, details={"campus_id": cursor.lastrowid, "code": code})
                            connection.commit()
                            flash("Campus added to this institution.", "success")
                        except sqlite3.IntegrityError:
                            connection.rollback()
                            flash("That campus code already exists for this school.", "danger")
                elif action == "update_profile":
                    editable = ("legal_name", "classification", "ownership_type", "owner_name",
                                "registration_number", "website_url", "description", "address_line1",
                                "address_line2", "city", "county", "region", "postal_code",
                                "country_code", "timezone", "contact_email", "contact_phone")
                    changes = {key: request.form.get(key, "") for key in editable if key in request.form}
                    merged = {key: school[key] for key in school.keys()}
                    merged.update(changes)
                    checked, errors = _validate_school(merged, require_registration=False)
                    if errors:
                        for error in errors:
                            flash(error, "danger")
                    else:
                        connection.execute(
                            "UPDATE schools SET " + ", ".join(key + "=?" for key in changes) +
                            ", updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (*[checked[key] for key in changes], school_id),
                        )
                        _audit(connection, "school.profile_updated", school_id, details={"fields": sorted(changes)})
                        connection.commit()
                        flash("Institution profile updated.", "success")
                else:
                    abort(400)
                return redirect(url_for("school_network.school_profile", school_id=school_id))
            campuses = connection.execute("SELECT * FROM campuses WHERE school_id=? ORDER BY is_main DESC, name", (school_id,)).fetchall()
            contacts = connection.execute("SELECT * FROM school_contacts WHERE school_id=? ORDER BY is_primary DESC, id", (school_id,)).fetchall()
            return render_template("school_network/profile.html", school=school, campuses=campuses,
                                   contacts=contacts, platform_access=(access == "platform"),
                                   classifications=sorted(CLASSIFICATIONS), ownership_types=sorted(OWNERSHIP_TYPES))
        finally:
            connection.close()

    @bp.post("/platform/schools/<int:school_id>/review")
    @login_required
    def platform_school_review_form(school_id):
        if not _is_platform_owner():
            abort(403)
        action = _clean(request.form.get("action"), 24).lower()
        # Delegate to the same transition rules as the API via an internal request-free helper.
        transitions = {
            "start_review": ({"pending"}, "under_review", "suspended"),
            "approve": ({"pending", "under_review"}, "approved", "suspended"),
            "reject": ({"pending", "under_review"}, "rejected", "suspended"),
            "activate": ({"approved"}, "active", "active"),
            "suspend": ({"active"}, "suspended", "suspended"),
            "reactivate": ({"suspended"}, "active", "active"),
            "archive": ({"rejected", "suspended"}, "archived", "suspended"),
        }
        connection = connection_factory()
        try:
            row = connection.execute("SELECT lifecycle_status FROM schools WHERE id=?", (school_id,)).fetchone()
            if row is None:
                abort(404)
            transition = transitions.get(action)
            if not transition or row["lifecycle_status"] not in transition[0]:
                flash("That institution lifecycle transition is not permitted.", "danger")
            else:
                connection.execute(
                    "UPDATE schools SET lifecycle_status=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (transition[1], transition[2], school_id),
                )
                _audit(connection, "school." + action, school_id, details={"from": row["lifecycle_status"], "to": transition[1]})
                connection.commit()
                flash("Institution status updated.", "success")
        finally:
            connection.close()
        return redirect(url_for("school_network.platform_schools"))

    return bp
