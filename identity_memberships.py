"""Multi-school identity and membership lifecycle for the existing Flask portal.

A platform account is global; school memberships carry school/campus roles.
Legacy users are backfilled additively. Password hashes and account IDs are not
rewritten. Platform-owner accounts remain platform-level and separate.
"""
import hashlib
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from flask import Blueprint, abort, current_app, jsonify, request, session, url_for
from flask_login import current_user, login_required, login_user
from werkzeug.security import check_password_hash, generate_password_hash


SCHOOL_ROLES = {
    "school_admin", "teacher", "registrar", "principal", "vice_principal",
    "student", "parent", "admissions_officer", "academic_admin",
    "attendance_officer", "finance_admin", "librarian", "counselor",
}


def _now():
    return datetime.now(timezone.utc)


def _stamp(value=None):
    return (value or _now()).isoformat(timespec="seconds")


def _hash_token(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _send_security_email(recipient, subject, body):
    """Send security mail only when SMTP is explicitly configured; never log tokens."""
    import os
    host = os.environ.get("SMTP_HOST", "").strip()
    sender = os.environ.get("SMTP_FROM_EMAIL", "").strip()
    if not host or not sender:
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        if username:
            server.login(username, password)
        server.send_message(message)
    return True


def initialize_membership_schema(connection_factory):
    """Idempotent additive migration; retain all legacy identity credentials."""
    connection = connection_factory()
    try:
        connection.execute("BEGIN IMMEDIATE")
        user_columns = {r["name"] for r in connection.execute("PRAGMA table_info(users)")}
        if "email" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS school_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                school_id INTEGER NOT NULL REFERENCES schools(id),
                campus_id INTEGER,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('invited','active','suspended','revoked')),
                is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
                invited_by INTEGER REFERENCES users(id),
                accepted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (school_id, campus_id) REFERENCES campuses(school_id, id)
            )
        """)
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_scope_unique
            ON school_memberships(user_id, school_id, COALESCE(campus_id, 0))
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_memberships_user_status
            ON school_memberships(user_id, status, is_primary)
        """)
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
            ON users(lower(email)) WHERE email IS NOT NULL AND trim(email) <> ''
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS membership_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                school_id INTEGER NOT NULL REFERENCES schools(id),
                campus_id INTEGER,
                role TEXT NOT NULL,
                invited_by INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','accepted','revoked','expired')),
                expires_at TEXT NOT NULL,
                accepted_by INTEGER REFERENCES users(id),
                accepted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (school_id, campus_id) REFERENCES campuses(school_id, id)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_membership_invites_email_status
            ON membership_invitations(lower(email), status, expires_at)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS identity_recovery_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                consumed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS identity_security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                school_id INTEGER REFERENCES schools(id),
                event_type TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Existing one-school accounts become members of their current school.
        # No IDs, password hashes, account statuses, or legacy role values change.
        connection.execute("""
            INSERT OR IGNORE INTO school_memberships
                (user_id, school_id, role, status, is_primary, accepted_at)
            SELECT id, school_id, role, 'active', 1, COALESCE(approved_at, created_at)
            FROM users
            WHERE school_id IS NOT NULL AND is_super_admin = 0
              AND role IN (
                'school_admin','teacher','registrar','principal','vice_principal',
                'student','parent','admissions_officer','academic_admin',
                'attendance_officer','finance_admin','librarian','counselor'
              )
        """)
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_one_primary
            ON school_memberships(user_id) WHERE is_primary=1 AND status='active'
        """)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def resolve_membership(connection, user_id, session_data):
    """Resolve only a live membership owned by this authenticated platform identity."""
    selected = session_data.get("active_membership_id")
    if selected is not None:
        if not isinstance(selected, int) or isinstance(selected, bool):
            return None
        return connection.execute("""
            SELECT m.id AS membership_id, m.school_id, m.campus_id, m.role,
                   m.status AS membership_status
            FROM school_memberships m
            JOIN schools s ON s.id=m.school_id AND s.status='active'
            WHERE m.id=? AND m.user_id=? AND m.status='active'
              AND (m.campus_id IS NULL OR EXISTS (
                SELECT 1 FROM campuses c WHERE c.id=m.campus_id
                  AND c.school_id=m.school_id AND c.status='active'
              ))
        """, (selected, user_id)).fetchone()
    row = connection.execute("""
        SELECT m.id AS membership_id, m.school_id, m.campus_id, m.role,
               m.status AS membership_status
        FROM school_memberships m
        JOIN schools s ON s.id=m.school_id AND s.status='active'
        WHERE m.user_id=? AND m.status='active'
          AND (m.campus_id IS NULL OR EXISTS (
            SELECT 1 FROM campuses c WHERE c.id=m.campus_id
              AND c.school_id=m.school_id AND c.status='active'
          ))
        ORDER BY m.is_primary DESC, m.id LIMIT 1
    """, (user_id,)).fetchone()
    if row is not None:
        session_data["active_membership_id"] = int(row["membership_id"])
    return row


def create_membership_blueprint(connection_factory):
    bp = Blueprint("identity_memberships", __name__)

    def _record(connection, user_id, school_id, event, details=""):
        connection.execute(
            "INSERT INTO identity_security_events(user_id,school_id,event_type,details) VALUES(?,?,?,?)",
            (user_id, school_id, event, details[:300]),
        )

    def _school_admin(connection, school_id):
        if (not current_user.is_authenticated or
            current_user.school_id != school_id or
            current_user.role not in {"school_admin", "principal"}):
            abort(403)
        school = connection.execute(
            "SELECT id FROM schools WHERE id=? AND status='active'", (school_id,)
        ).fetchone()
        if not school:
            abort(404)

    @bp.get("/api/v1/identity/memberships")
    @login_required
    def my_memberships():
        connection = connection_factory()
        try:
            rows = connection.execute("""
                SELECT m.id, m.school_id, s.name AS school_name, m.campus_id,
                       c.name AS campus_name, m.role, m.status, m.is_primary
                FROM school_memberships m
                JOIN schools s ON s.id=m.school_id
                LEFT JOIN campuses c ON c.id=m.campus_id AND c.school_id=m.school_id
                WHERE m.user_id=? AND m.status='active' AND s.status='active'
                  AND (m.campus_id IS NULL OR c.status='active')
                ORDER BY m.is_primary DESC, s.name, c.name
            """, (int(current_user.id),)).fetchall()
            return jsonify({"memberships": [dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.post("/api/v1/identity/memberships/switch")
    @login_required
    def switch_membership():
        payload = request.get_json(silent=True) or request.form
        raw_id = payload.get("membership_id")
        if not str(raw_id or "").isdigit():
            return jsonify({"error": "A valid membership_id is required."}), 400
        connection = connection_factory()
        try:
            row = connection.execute("""
                SELECT m.id, m.school_id, m.campus_id
                FROM school_memberships m
                JOIN schools s ON s.id=m.school_id AND s.status='active'
                WHERE m.id=? AND m.user_id=? AND m.status='active'
                  AND (m.campus_id IS NULL OR EXISTS (
                    SELECT 1 FROM campuses c WHERE c.id=m.campus_id
                      AND c.school_id=m.school_id AND c.status='active'
                  ))
            """, (int(raw_id), int(current_user.id))).fetchone()
            if row is None:
                _record(connection, int(current_user.id), None, "membership_switch_denied")
                connection.commit()
                return jsonify({"error": "Membership is unavailable."}), 403
            _record(connection, int(current_user.id), row["school_id"], "membership_switched")
            connection.commit()
            user_id = current_user.id
            # Drop all prior workspace/session state, then establish a fresh
            # authenticated session bound to the server-verified membership.
            session.clear()
            session["active_membership_id"] = int(row["id"])
            session.permanent = True
            from flask_login import current_user as _current_user
            # Reload through Flask-Login's user loader on the next request.
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            session["_id"] = None
            return jsonify({"status": "switched", "school_id": row["school_id"],
                            "campus_id": row["campus_id"]})
        finally:
            connection.close()

    @bp.post("/api/v1/identity/invitations")
    @login_required
    def invite_member():
        payload = request.get_json(silent=True) or {}
        email = str(payload.get("email", "")).strip().lower()
        role = str(payload.get("role", "")).strip()
        campus_id = payload.get("campus_id")
        if len(email) > 254 or "@" not in email or role not in SCHOOL_ROLES:
            return jsonify({"error": "A valid email and school role are required."}), 400
        if role in {"school_admin", "principal"} and current_user.role != "school_admin":
            abort(403)
        school_id = current_user.school_id
        if not school_id:
            abort(403)
        token = secrets.token_urlsafe(32)
        connection = connection_factory()
        try:
            _school_admin(connection, school_id)
            if campus_id is not None:
                if not isinstance(campus_id, int) or isinstance(campus_id, bool):
                    return jsonify({"error": "Invalid campus."}), 400
                if not connection.execute(
                    "SELECT 1 FROM campuses WHERE id=? AND school_id=? AND status='active'",
                    (campus_id, school_id),
                ).fetchone():
                    abort(404)
            expires = _stamp(_now() + timedelta(days=7))
            connection.execute("""
                INSERT INTO membership_invitations
                    (email,token_hash,school_id,campus_id,role,invited_by,expires_at)
                VALUES(?,?,?,?,?,?,?)
            """, (email, _hash_token(token), school_id, campus_id, role,
                  int(current_user.id), expires))
            _record(connection, int(current_user.id), school_id, "membership_invited", role)
            connection.commit()
            link = url_for("identity_memberships.accept_invitation", _external=True) + "?token=" + token
            try:
                sent = _send_security_email(email, "School membership invitation",
                    "You have been invited to join a school workspace. Accept within 7 days:\\n" + link)
            except Exception:
                current_app.logger.exception("Membership invitation delivery failed.")
                sent = False
            if not sent:
                connection.execute(
                    "UPDATE membership_invitations SET status='revoked' WHERE token_hash=?",
                    (_hash_token(token),),
                )
                connection.commit()
                return jsonify({"error": "Invitation email could not be delivered; SMTP is not configured or delivery failed."}), 503
            return jsonify({"status": "sent", "expires_at": expires}), 201
        except sqlite3.IntegrityError:
            connection.rollback()
            return jsonify({"error": "An equivalent invitation or membership already exists."}), 409
        finally:
            connection.close()

    @bp.get("/identity/invitations/accept")
    def invitation_landing():
        token = str(request.args.get("token", "")).strip()
        if len(token) < 32:
            return jsonify({"error": "Invitation is invalid or expired."}), 400
        return jsonify({
            "message": "Invitation link received. Sign in to the invited email account, then submit this token to the invitation acceptance endpoint.",
            "accept_endpoint": url_for("identity_memberships.accept_invitation"),
            "token": token,
        })

    @bp.post("/identity/invitations/accept")
    def accept_invitation():
        payload = request.get_json(silent=True) or request.form
        token = str(payload.get("token", "")).strip()
        if len(token) < 32 or len(token) > 256:
            return jsonify({"error": "Invitation is invalid or expired."}), 400
        connection = connection_factory()
        try:
            invite = connection.execute("""
                SELECT * FROM membership_invitations
                WHERE token_hash=? AND status='pending'
            """, (_hash_token(token),)).fetchone()
            if invite is None or invite["expires_at"] <= _stamp():
                return jsonify({"error": "Invitation is invalid or expired."}), 400
            if current_user.is_authenticated:
                user_id = int(current_user.id)
                user = connection.execute(
                    "SELECT email,status FROM users WHERE id=?", (user_id,)
                ).fetchone()
                if not user or user["status"] != "active" or (user["email"] or "").lower() != invite["email"]:
                    return jsonify({"error": "Sign in with the invited email address."}), 403
            else:
                existing = connection.execute(
                    "SELECT id FROM users WHERE lower(email)=? AND status='active'",
                    (invite["email"],),
                ).fetchone()
                if existing:
                    return jsonify({"error": "Sign in to the existing account before accepting this invitation."}), 403
                username = str(payload.get("username", "")).strip()
                full_name = str(payload.get("full_name", "")).strip()
                password = str(payload.get("password", ""))
                if (not username or len(username) > 64 or
                    not username.replace("_", "").replace("-", "").isalnum() or
                    not full_name or len(full_name) > 120 or len(password) < 12):
                    return jsonify({"error": "Provide a valid username, full name and password of at least 12 characters."}), 400
                try:
                    cursor = connection.execute("""
                        INSERT INTO users(username,full_name,email,password_hash,role,status,is_super_admin,school_id)
                        VALUES(?,?,?,?,?,'active',0,?)
                    """, (username, full_name, invite["email"],
                          generate_password_hash(password), invite["role"], invite["school_id"]))
                    user_id = int(cursor.lastrowid)
                except sqlite3.IntegrityError:
                    return jsonify({"error": "Username already exists. Sign in to the existing account and accept the invitation."}), 409
            connection.execute("""
                INSERT OR IGNORE INTO school_memberships
                    (user_id,school_id,campus_id,role,status,is_primary,invited_by,accepted_at)
                VALUES(?,?,?,?,'active',0,?,?)
            """, (user_id, invite["school_id"], invite["campus_id"], invite["role"],
                  invite["invited_by"], _stamp()))
            connection.execute("""
                UPDATE membership_invitations SET status='accepted',accepted_by=?,accepted_at=?
                WHERE id=? AND status='pending'
            """, (user_id, _stamp(), invite["id"]))
            _record(connection, user_id, invite["school_id"], "membership_accepted", invite["role"])
            connection.commit()
            return jsonify({"status": "accepted", "message": "Membership is active. Sign in or switch to the school workspace."}), 200
        finally:
            connection.close()

    @bp.delete("/api/v1/identity/memberships/<int:membership_id>")
    @login_required
    def revoke_membership(membership_id):
        connection = connection_factory()
        try:
            row = connection.execute(
                "SELECT id,user_id,school_id,role,status FROM school_memberships WHERE id=?",
                (membership_id,),
            ).fetchone()
            if row is None:
                abort(404)
            _school_admin(connection, row["school_id"])
            if row["status"] != "active":
                return jsonify({"status": row["status"]}), 200
            if row["role"] == "school_admin":
                count = connection.execute("""
                    SELECT COUNT(*) FROM school_memberships
                    WHERE school_id=? AND role='school_admin' AND status='active'
                """, (row["school_id"],)).fetchone()[0]
                if count <= 1:
                    return jsonify({"error": "The last active school administrator cannot be revoked."}), 409
            connection.execute(
                "UPDATE school_memberships SET status='revoked',updated_at=? WHERE id=?",
                (_stamp(), membership_id),
            )
            _record(connection, int(current_user.id), row["school_id"], "membership_revoked", str(row["user_id"]))
            connection.commit()
            return jsonify({"status": "revoked"})
        finally:
            connection.close()

    @bp.post("/identity/recovery/request")
    def request_recovery():
        payload = request.get_json(silent=True) or request.form
        email = str(payload.get("email", "")).strip().lower()
        connection = connection_factory()
        try:
            user = connection.execute(
                "SELECT id,email FROM users WHERE lower(email)=? AND status='active'", (email,)
            ).fetchone() if email else None
            if user:
                token = secrets.token_urlsafe(32)
                expires = _stamp(_now() + timedelta(minutes=30))
                connection.execute(
                    "INSERT INTO identity_recovery_tokens(user_id,token_hash,expires_at) VALUES(?,?,?)",
                    (user["id"], _hash_token(token), expires),
                )
                connection.commit()
                link = url_for("identity_memberships.reset_recovery", _external=True) + "?token=" + token
                try:
                    _send_security_email(email, "Account recovery",
                        "Use this one-time link within 30 minutes to reset your password:\\n" + link)
                except Exception:
                    current_app.logger.exception("Account recovery email delivery failed.")
            return jsonify({"message": "If an eligible account exists, recovery instructions will be sent."}), 202
        finally:
            connection.close()

    @bp.get("/identity/recovery/reset")
    def recovery_landing():
        token = str(request.args.get("token", "")).strip()
        if len(token) < 32:
            return jsonify({"error": "Recovery link is invalid or expired."}), 400
        return jsonify({
            "message": "Submit a new password and this one-time token to the recovery reset endpoint.",
            "reset_endpoint": url_for("identity_memberships.reset_recovery"),
            "token": token,
        })

    @bp.post("/identity/recovery/reset")
    def reset_recovery():
        payload = request.get_json(silent=True) or request.form
        token = str(payload.get("token", "")).strip()
        password = str(payload.get("password", ""))
        if len(password) < 12 or len(token) < 32:
            return jsonify({"error": "A valid recovery token and password of at least 12 characters are required."}), 400
        connection = connection_factory()
        try:
            row = connection.execute("""
                SELECT t.id,t.user_id FROM identity_recovery_tokens t
                JOIN users u ON u.id=t.user_id AND u.status='active'
                WHERE t.token_hash=? AND t.consumed_at IS NULL AND t.expires_at>?
            """, (_hash_token(token), _stamp())).fetchone()
            if row is None:
                return jsonify({"error": "Recovery token is invalid or expired."}), 400
            connection.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(password), row["user_id"]),
            )
            connection.execute(
                "UPDATE identity_recovery_tokens SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL",
                (_stamp(), row["user_id"]),
            )
            connection.execute(
                "UPDATE identity_security_events SET details='password reset' WHERE user_id=? AND event_type='recovery_requested'",
                (row["user_id"],),
            )
            _record(connection, row["user_id"], None, "password_recovered")
            connection.commit()
            return jsonify({"status": "password_updated", "message": "Password updated. Sign in again."})
        finally:
            connection.close()

    return bp
