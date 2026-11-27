"""Scoped RBAC + ABAC authorization for the Digital Education Ecosystem.

Authorization is evaluated server-side on every protected operation. Roles provide
configurable baseline permissions; scoped grants may add permissions, while any
matching explicit deny overrides inherited allows. Tenant, campus, identity and
resource attributes are independently checked (ABAC).
"""
import json
from functools import wraps

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import current_user, login_required


SCOPE_TYPES = ("platform", "organization", "school", "campus")
EFFECTS = ("allow", "deny")

PERMISSIONS = {
    "platform.manage_schools": "platform",
    "platform.manage_operators": "platform",
    "platform.manage_global_policies": "platform",
    "organization.manage_schools": "organization",
    "organization.view_analytics": "organization",
    "school.view_dashboard": "school",
    "school.manage_profile": "school",
    "school.view_configuration": "school",
    "school.manage_configuration": "school",
    "school.manage_campuses": "school",
    "school.manage_memberships": "school",
    "school.manage_roles": "school",
    "school.view_staff": "school",
    "school.manage_staff": "school",
    "school.manage_academic_calendar": "school",
    "school.manage_grading": "school",
    "academic.view_records": "school",
    "academic.manage_records": "school",
    "academic.manage_attendance": "school",
    "academic.publish_results": "school",
    "academic.manage_classes": "school",
    "finance.view": "school",
    "finance.manage_fees": "school",
    "finance.record_payments": "school",
    "finance.refund_payments": "school",
    "finance.export": "school",
    "learning.view": "school",
    "learning.manage_courses": "school",
    "learning.submit_work": "school",
    "communication.view": "school",
    "communication.manage": "school",
    "student.view_own": "school",
    "student.manage_own_profile": "school",
    "parent.view_linked_children": "school",
    "provider.access_assigned_services": "campus",
    "audit.view": "school",
}

# Default role sets are deliberately bounded. These rows are editable by
# authorized policy administrators; a role never receives an implicit wildcard.
DEFAULT_ROLE_PERMISSIONS = {
    "platform_admin": set(PERMISSIONS),
    "super_admin": set(PERMISSIONS),
    "school_owner": {
        "school.view_dashboard", "school.manage_profile", "school.view_configuration", "school.manage_configuration",
        "school.manage_campuses", "school.manage_memberships", "school.manage_roles",
        "school.view_staff", "school.manage_staff", "school.manage_academic_calendar",
        "school.manage_grading", "academic.view_records", "academic.manage_records",
        "academic.manage_attendance", "academic.publish_results", "academic.manage_classes",
        "finance.view", "finance.manage_fees", "finance.record_payments",
        "finance.refund_payments", "finance.export", "learning.view",
        "learning.manage_courses", "communication.view", "communication.manage", "audit.view",
    },
    "school_admin": {
        "school.view_dashboard", "school.manage_profile", "school.view_configuration", "school.manage_configuration",
        "school.manage_campuses", "school.manage_memberships", "school.manage_roles",
        "school.view_staff", "school.manage_staff", "school.manage_academic_calendar",
        "school.manage_grading", "academic.view_records", "academic.manage_records",
        "academic.manage_attendance", "academic.publish_results", "academic.manage_classes",
        "finance.view", "finance.manage_fees", "finance.record_payments",
        "finance.refund_payments", "finance.export", "learning.view",
        "learning.manage_courses", "communication.view", "communication.manage", "audit.view",
    },
    "principal": {
        "school.view_dashboard", "school.view_configuration", "school.view_staff", "school.manage_staff",
        "school.manage_academic_calendar", "school.manage_grading", "academic.view_records",
        "academic.manage_records", "academic.manage_attendance", "academic.publish_results",
        "academic.manage_classes", "finance.view", "finance.manage_fees",
        "finance.record_payments", "learning.view", "learning.manage_courses",
        "communication.view", "communication.manage", "audit.view",
    },
    "vice_principal": {
        "school.view_dashboard", "school.view_staff", "academic.view_records",
        "academic.manage_records", "academic.manage_attendance", "academic.manage_classes",
        "learning.view", "communication.view", "communication.manage",
    },
    "registrar": {
        "school.view_dashboard", "academic.view_records", "academic.manage_records",
        "academic.manage_attendance", "academic.publish_results", "academic.manage_classes",
        "communication.view",
    },
    "teacher": {
        "school.view_dashboard", "academic.view_records", "academic.manage_attendance",
        "learning.view", "learning.manage_courses", "learning.submit_work", "communication.view",
    },
    "finance_officer": {
        "school.view_dashboard", "finance.view", "finance.manage_fees",
        "finance.record_payments", "finance.export",
    },
    "finance_admin": {
        "school.view_dashboard", "finance.view", "finance.manage_fees",
        "finance.record_payments", "finance.refund_payments", "finance.export",
    },
    "student": {
        "school.view_dashboard", "student.view_own", "student.manage_own_profile",
        "academic.view_records", "learning.view", "learning.submit_work",
        "communication.view",
    },
    "parent": {
        "school.view_dashboard", "parent.view_linked_children", "academic.view_records",
        "finance.view", "communication.view",
    },
    "external_service_provider": {"provider.access_assigned_services"},
    "admissions_officer": {"school.view_dashboard", "school.view_staff", "academic.view_records", "academic.manage_records"},
    "academic_admin": {"school.view_dashboard", "school.view_staff", "academic.view_records", "academic.manage_records", "academic.manage_classes", "academic.publish_results"},
    "attendance_officer": {"school.view_dashboard", "academic.view_records", "academic.manage_attendance"},
    "librarian": {"school.view_dashboard", "academic.view_records", "communication.view"},
    "counselor": {"school.view_dashboard", "academic.view_records", "communication.view"},
}

ROLE_PARENTS = {
    "school_owner": ("school_admin",),
    "school_admin": ("principal",),
    "principal": ("vice_principal",),
    "vice_principal": (),
    "registrar": (),
    "teacher": (),
    "finance_officer": (),
    "finance_admin": (),
    "student": (),
    "parent": (),
    "external_service_provider": (),
    "admissions_officer": (),
    "academic_admin": (),
    "attendance_officer": (),
    "librarian": (),
    "counselor": (),
    "platform_admin": ("super_admin",),
    "super_admin": (),
}


def initialize_authorization_schema(connection_factory):
    connection = connection_factory()
    try:
        connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS authorization_role_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            permission TEXT NOT NULL,
            scope_type TEXT NOT NULL CHECK(scope_type IN ('platform','organization','school','campus')),
            effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
            updated_by INTEGER REFERENCES users(id),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(role,permission,scope_type)
        );
        CREATE TABLE IF NOT EXISTS authorization_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL CHECK(subject_type IN ('user','role')),
            subject_id TEXT NOT NULL,
            permission TEXT NOT NULL,
            scope_type TEXT NOT NULL CHECK(scope_type IN ('platform','organization','school','campus')),
            scope_id INTEGER,
            effect TEXT NOT NULL CHECK(effect IN ('allow','deny')),
            conditions_json TEXT NOT NULL DEFAULT '{}',
            expires_at TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            revoked_at TEXT,
            revoked_by INTEGER REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_authz_grants_lookup
          ON authorization_grants(subject_type,subject_id,permission,scope_type,scope_id,revoked_at);
        CREATE TABLE IF NOT EXISTS authorization_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER REFERENCES users(id),
            school_id INTEGER REFERENCES schools(id),
            campus_id INTEGER,
            action TEXT NOT NULL,
            permission TEXT,
            subject_type TEXT,
            subject_id TEXT,
            effect TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)
        for role, permissions in DEFAULT_ROLE_PERMISSIONS.items():
            for permission in permissions:
                scope = PERMISSIONS[permission]
                connection.execute("""
                    INSERT OR IGNORE INTO authorization_role_permissions
                    (role,permission,scope_type,effect) VALUES(?,?,?,'allow')
                """, (role, permission, scope))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _role_chain(role):
    seen, stack = set(), [role]
    while stack:
        value = stack.pop()
        if value in seen:
            continue
        seen.add(value)
        stack.extend(ROLE_PARENTS.get(value, ()))
    return seen


def _scope_chain(connection, school_id, campus_id):
    chain = [("platform", None)]
    if school_id is None:
        return chain
    group = connection.execute(
        "SELECT group_id FROM school_group_memberships WHERE school_id=?",
        (school_id,),
    ).fetchone()
    if group:
        chain.append(("organization", int(group["group_id"])))
    chain.append(("school", int(school_id)))
    if campus_id is not None:
        chain.append(("campus", int(campus_id)))
    return chain


def _active(value):
    return str(value or "").lower() in {"1", "true", "yes", "active"}


def has_permission(connection, permission, school_id=None, campus_id=None,
                   resource_owner_id=None, attributes=None):
    """Evaluate RBAC, scope inheritance, explicit deny and ABAC constraints."""
    if not current_user.is_authenticated or permission not in PERMISSIONS:
        return False
    attrs = attributes or {}
    actor_id = int(current_user.id)
    is_platform = bool(getattr(current_user, "is_super_admin", False)
                       and getattr(current_user, "role", "") in {"super_admin", "platform_admin"}
                       and getattr(current_user, "school_id", None) is None)
    if is_platform:
        actor_school = None
        actor_campus = None
        role = "super_admin"
        # Platform identities still require explicit school selection for tenant work.
        from flask import session
        selected_school = session.get("active_school_id")
        if school_id is None and PERMISSIONS[permission] != "platform":
            return False
        if selected_school is not None and school_id is not None and int(selected_school) != int(school_id):
            return False
    else:
        actor_school = getattr(current_user, "school_id", None)
        actor_campus = getattr(current_user, "campus_id", None)
        role = getattr(current_user, "role", "")
        if actor_school is None or school_id is None or int(actor_school) != int(school_id):
            return False
        if actor_campus is not None and campus_id is not None and int(actor_campus) != int(campus_id):
            return False
        if actor_campus is not None and campus_id is None and PERMISSIONS[permission] == "campus":
            return False
        membership_id = getattr(current_user, "membership_id", None)
        member = connection.execute("""
            SELECT m.status,m.role,m.school_id,m.campus_id,u.status AS user_status,
                   s.status AS school_status,s.lifecycle_status
            FROM school_memberships m JOIN users u ON u.id=m.user_id
            JOIN schools s ON s.id=m.school_id
            WHERE m.id=? AND m.user_id=?
        """, (membership_id, actor_id)).fetchone()
        if (not member or member["status"] != "active" or member["user_status"] != "active"
                or member["school_status"] != "active" or member["lifecycle_status"] != "active"):
            return False
        if member["campus_id"] is not None and campus_id is not None and int(member["campus_id"]) != int(campus_id):
            return False
    if resource_owner_id is not None and permission in {"student.view_own", "student.manage_own_profile", "parent.view_linked_children"} and int(resource_owner_id) != actor_id:
        return False
    if attrs.get("owner_user_id") is not None and int(attrs["owner_user_id"]) != actor_id:
        if permission in {"student.view_own", "student.manage_own_profile", "parent.view_linked_children"}:
            return False
    if attrs.get("assigned_user_id") is not None and int(attrs["assigned_user_id"]) != actor_id:
        return False
    if attrs.get("resource_school_id") is not None and (school_id is None or int(attrs["resource_school_id"]) != int(school_id)):
        return False
    if attrs.get("resource_campus_id") is not None and campus_id is not None and int(attrs["resource_campus_id"]) != int(campus_id):
        return False
    scope_chain = _scope_chain(connection, school_id, campus_id)
    roles = _role_chain(role)
    matches = []
    for scope_type, scope_id in scope_chain:
        for inherited_role in roles:
            row = connection.execute("""
                SELECT effect FROM authorization_role_permissions
                WHERE role=? AND permission=? AND scope_type=?
            """, (inherited_role, permission, scope_type)).fetchone()
            if row:
                matches.append((scope_type, scope_id, row["effect"]))
        for subject_type, subject_id in (("user", str(actor_id)), ("role", role)):
            grants = connection.execute("""
                SELECT effect,conditions_json FROM authorization_grants
                WHERE subject_type=? AND subject_id=? AND permission=? AND scope_type=?
                  AND (scope_id IS ? OR scope_id=?) AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP)
            """, (subject_type, subject_id, permission, scope_type, scope_id, scope_id)).fetchall()
            for grant in grants:
                try:
                    conditions = json.loads(grant["conditions_json"] or "{}")
                except (TypeError, ValueError):
                    return False
                if any(attrs.get(key) != value for key, value in conditions.items()):
                    continue
                matches.append((scope_type, scope_id, grant["effect"]))
    # Any explicit deny in the applicable inheritance chain wins over every allow.
    if any(effect == "deny" for _, _, effect in matches):
        return False
    return any(effect == "allow" for _, _, effect in matches)


def require_permission(permission, connection_factory, school_id=None, campus_id=None,
                       resource_owner_id=None, attributes=None, audit=True):
    if not current_user.is_authenticated:
        abort(401)
    resolved_school = school_id
    if resolved_school is None:
        resolved_school = getattr(current_user, "school_id", None)
    resolved_campus = campus_id
    if resolved_campus is None:
        resolved_campus = getattr(current_user, "campus_id", None)
    connection = connection_factory()
    try:
        allowed = has_permission(
            connection, permission, resolved_school, resolved_campus,
            resource_owner_id, attributes,
        )
        if audit:
            connection.execute("""
                INSERT INTO authorization_audit
                (actor_user_id,school_id,campus_id,action,permission,effect,details_json)
                VALUES(?,?,?,?,?,?,?)
            """, (int(current_user.id), resolved_school, resolved_campus,
                  "authorization.allowed" if allowed else "authorization.denied",
                  permission, "allow" if allowed else "deny",
                  json.dumps(attributes or {}, sort_keys=True)[:4000]))
            connection.commit()
    finally:
        connection.close()
    if not allowed:
        abort(403)
    return True


def create_authorization_blueprint(connection_factory):
    bp = Blueprint("authorization", __name__)

    @bp.get("/api/v1/authorization/me")
    @login_required
    def my_permissions():
        school_id = getattr(current_user, "school_id", None)
        campus_id = getattr(current_user, "campus_id", None)
        connection = connection_factory()
        try:
            effective = sorted(permission for permission in PERMISSIONS
                               if has_permission(connection, permission, school_id, campus_id))
            return jsonify({"school_id": school_id, "campus_id": campus_id,
                            "role": getattr(current_user, "role", None),
                            "permissions": effective})
        finally:
            connection.close()

    @bp.put("/api/v1/authorization/roles/<role>/permissions")
    @login_required
    def update_role_permissions(role):
        if not (getattr(current_user, "is_super_admin", False)
                and getattr(current_user, "role", None) == "super_admin"
                and getattr(current_user, "school_id", None) is None):
            abort(403)
        payload = request.get_json(silent=True) or {}
        permission = payload.get("permission")
        scope_type = payload.get("scope_type", "school")
        effect = payload.get("effect", "allow")
        if role not in DEFAULT_ROLE_PERMISSIONS or permission not in PERMISSIONS:
            return jsonify({"error": "Unknown role or permission."}), 400
        if scope_type not in SCOPE_TYPES or effect not in EFFECTS:
            return jsonify({"error": "Invalid scope or effect."}), 400
        if PERMISSIONS[permission] == "platform" and scope_type != "platform":
            return jsonify({"error": "Platform permissions cannot be assigned below platform scope."}), 403
        if scope_type == "platform" and role not in {"super_admin", "platform_admin"}:
            return jsonify({"error": "Only platform roles may receive platform-scope permissions."}), 403
        connection = connection_factory()
        try:
            connection.execute("""
                INSERT INTO authorization_role_permissions(role,permission,scope_type,effect,updated_by,updated_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(role,permission,scope_type) DO UPDATE SET
                  effect=excluded.effect,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP
            """, (role, permission, scope_type, effect, int(current_user.id)))
            connection.execute("""
                INSERT INTO authorization_audit(actor_user_id,action,permission,subject_type,subject_id,effect,details_json)
                VALUES(?,?,?,?,?,?,?)
            """, (int(current_user.id), "role_permission.updated", permission,
                  "role", role, effect, json.dumps({"scope_type": scope_type})))
            connection.commit()
            return jsonify({"role": role, "permission": permission,
                            "scope_type": scope_type, "effect": effect})
        finally:
            connection.close()

    @bp.post("/api/v1/authorization/grants")
    @login_required
    def create_grant():
        payload = request.get_json(silent=True) or {}
        permission = payload.get("permission")
        scope_type = payload.get("scope_type")
        scope_id = payload.get("scope_id")
        effect = payload.get("effect", "allow")
        subject_type = payload.get("subject_type")
        subject_id = str(payload.get("subject_id", "")).strip()
        if permission not in PERMISSIONS or scope_type not in SCOPE_TYPES or effect not in EFFECTS:
            return jsonify({"error": "Unknown permission, scope or effect."}), 400
        if subject_type not in {"user", "role"} or not subject_id:
            return jsonify({"error": "A user or role subject is required."}), 400
        if subject_type == "role" and subject_id not in DEFAULT_ROLE_PERMISSIONS:
            return jsonify({"error": "Unknown authorization role."}), 400
        if scope_type in {"platform", "organization"}:
            if not (getattr(current_user, "is_super_admin", False)
                    and getattr(current_user, "role", None) == "super_admin"
                    and getattr(current_user, "school_id", None) is None):
                abort(403)
            if scope_type == "platform" and PERMISSIONS[permission] != "platform":
                return jsonify({"error": "Platform grants are reserved for platform-scope permissions."}), 403
            if scope_type == "organization" and PERMISSIONS[permission] == "platform":
                return jsonify({"error": "Platform permissions cannot be assigned to organizations."}), 403
        else:
            school_id = payload.get("school_id")
            if not isinstance(school_id, int) or isinstance(school_id, bool):
                return jsonify({"error": "An authorized school_id is required."}), 400
            require_permission("school.manage_roles", connection_factory, school_id=school_id)
            scope_rank = {"platform": 0, "organization": 1, "school": 2, "campus": 3}
            if scope_rank[scope_type] < scope_rank[PERMISSIONS[permission]]:
                return jsonify({"error": "The grant scope is broader than this permission's permitted scope."}), 403
            connection = connection_factory()
            try:
                school = connection.execute("SELECT id FROM schools WHERE id=?", (school_id,)).fetchone()
                if not school:
                    abort(404)
                if scope_type == "school" and (not isinstance(scope_id, int) or scope_id != school_id):
                    return jsonify({"error": "Grant scope must match the authorized school."}), 403
                if scope_type == "organization":
                    valid = connection.execute(
                        "SELECT 1 FROM school_group_memberships WHERE group_id=? AND school_id=?",
                        (scope_id, school_id),
                    ).fetchone()
                    if not valid:
                        abort(404)
                if scope_type == "campus":
                    valid = connection.execute("SELECT 1 FROM campuses WHERE id=? AND school_id=?",
                                               (scope_id, school_id)).fetchone()
                    if not valid:
                        abort(404)
                if subject_type == "user":
                    valid_subject = connection.execute(
                        "SELECT 1 FROM school_memberships WHERE user_id=? AND school_id=? AND status='active'",
                        (int(subject_id), school_id),
                    ).fetchone()
                    if not valid_subject:
                        return jsonify({"error": "User subject must have an active membership in this school."}), 404
            finally:
                connection.close()
        conditions = payload.get("conditions", {})
        if not isinstance(conditions, dict) or len(json.dumps(conditions)) > 4000:
            return jsonify({"error": "Invalid grant conditions."}), 400
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            from datetime import datetime
            try:
                parsed_expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                if parsed_expiry.tzinfo is None:
                    return jsonify({"error": "Grant expiry must include a timezone."}), 400
                expires_at = parsed_expiry.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                return jsonify({"error": "Grant expiry must be a valid timezone-aware ISO timestamp."}), 400
        connection = connection_factory()
        try:
            cursor = connection.execute("""
                INSERT INTO authorization_grants
                (subject_type,subject_id,permission,scope_type,scope_id,effect,conditions_json,expires_at,created_by)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (subject_type, subject_id, permission, scope_type, scope_id, effect,
                  json.dumps(conditions, sort_keys=True), expires_at, int(current_user.id)))
            connection.execute("""
                INSERT INTO authorization_audit(actor_user_id,school_id,campus_id,action,permission,
                  subject_type,subject_id,effect,details_json)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (int(current_user.id), payload.get("school_id"), scope_id if scope_type == "campus" else None,
                  "grant.created", permission, subject_type, subject_id, effect,
                  json.dumps({"scope_type": scope_type, "conditions": conditions})))
            connection.commit()
            return jsonify({"grant_id": cursor.lastrowid, "status": "created"}), 201
        finally:
            connection.close()

    @bp.delete("/api/v1/authorization/grants/<int:grant_id>")
    @login_required
    def revoke_grant(grant_id):
        connection = connection_factory()
        try:
            grant = connection.execute("SELECT * FROM authorization_grants WHERE id=? AND revoked_at IS NULL",
                                       (grant_id,)).fetchone()
            if not grant:
                abort(404)
            if grant["scope_type"] == "platform":
                if not (getattr(current_user, "is_super_admin", False)
                        and getattr(current_user, "role", None) == "super_admin"):
                    abort(403)
            else:
                require_permission("school.manage_roles", connection_factory,
                                   school_id=grant["scope_id"] if grant["scope_type"] == "school" else request.args.get("school_id", type=int))
            connection.execute("UPDATE authorization_grants SET revoked_at=CURRENT_TIMESTAMP,revoked_by=? WHERE id=?",
                               (int(current_user.id), grant_id))
            connection.execute("""
                INSERT INTO authorization_audit(actor_user_id,school_id,action,permission,subject_type,subject_id,effect)
                VALUES(?,?,?,?,?,?,?)
            """, (int(current_user.id), grant["scope_id"] if grant["scope_type"] == "school" else None,
                  "grant.revoked", grant["permission"], grant["subject_type"], grant["subject_id"], grant["effect"]))
            connection.commit()
            return jsonify({"grant_id": grant_id, "status": "revoked"})
        finally:
            connection.close()

    return bp
