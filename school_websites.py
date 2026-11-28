"""Centralized, tenant-aware public school website management.

Public website content is deliberately isolated from ERP tables. Public rendering
reads only published website/page records and a small allow-list of institutional
profile fields. All schema changes are additive and idempotent.
"""
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, jsonify, render_template_string, request
from flask_login import current_user, login_required


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
WEBSITE_STATES = {"draft", "published", "suspended", "archived"}
PAGE_STATES = {"draft", "published", "archived"}
DEFAULT_THEME = {
    "primary_color": "#174A7E",
    "secondary_color": "#E8A838",
    "logo_url": "",
    "tagline": "",
    "footer_text": "",
}


def initialize_school_website_schema(connection_factory):
    """Create isolated website tables without modifying existing school/ERP rows."""
    connection = connection_factory()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS school_websites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','published','suspended','archived')),
            owner_user_id INTEGER REFERENCES users(id),
            theme_json TEXT NOT NULL DEFAULT '{}',
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_at TEXT,
            UNIQUE(slug),
            UNIQUE(school_id, id)
        );
        CREATE INDEX IF NOT EXISTS idx_school_websites_public
            ON school_websites(slug,status,school_id);
        CREATE TABLE IF NOT EXISTS school_website_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            website_id INTEGER NOT NULL,
            school_id INTEGER NOT NULL,
            slug TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','published','archived')),
            is_home INTEGER NOT NULL DEFAULT 0 CHECK(is_home IN (0,1)),
            show_in_navigation INTEGER NOT NULL DEFAULT 1 CHECK(show_in_navigation IN (0,1)),
            navigation_order INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(school_id,website_id)
                REFERENCES school_websites(school_id,id) ON DELETE CASCADE,
            UNIQUE(website_id,slug),
            UNIQUE(website_id,id)
        );
        CREATE INDEX IF NOT EXISTS idx_website_pages_public
            ON school_website_pages(website_id,status,navigation_order);
        CREATE TABLE IF NOT EXISTS school_website_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            website_id INTEGER NOT NULL,
            school_id INTEGER NOT NULL,
            hostname TEXT NOT NULL UNIQUE,
            verification_token TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','verified','disabled')),
            verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(school_id,website_id)
                REFERENCES school_websites(school_id,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS school_website_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            website_id INTEGER REFERENCES school_websites(id),
            actor_user_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_website_audit_tenant
            ON school_website_audit(school_id,website_id,created_at);
        """)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _json_object(value, default=None):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else (default or {})
        except (TypeError, ValueError):
            pass
    return default or {}


def _audit(connection, school_id, website_id, action, details=None):
    connection.execute(
        """INSERT INTO school_website_audit
           (school_id,website_id,actor_user_id,action,details_json)
           VALUES(?,?,?,?,?)""",
        (school_id, website_id, current_user.id if current_user.is_authenticated else None,
         action, json.dumps(details or {}, sort_keys=True)),
    )


def _tenant_id(require_school_context):
    if not current_user.is_authenticated:
        abort(401)
    school_id = require_school_context()
    if not school_id:
        abort(403)
    return int(school_id)


def _permission(name, connection_factory, school_id):
    from authorization import require_permission
    require_permission(name, connection_factory, school_id=school_id)


def _website_row(connection, school_id, website_id):
    row = connection.execute(
        "SELECT * FROM school_websites WHERE id=? AND school_id=?",
        (website_id, school_id),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _authorize_website(connection_factory, website, school_id, permission="school.manage_websites"):
    if int(website["school_id"]) != int(school_id):
        abort(404)
    if int(website["owner_user_id"] or -1) == int(current_user.id):
        return
    _permission(permission, connection_factory, school_id)


def _clean_theme(theme, existing=None):
    result = dict(DEFAULT_THEME)
    result.update(_json_object(existing))
    if not isinstance(theme, dict):
        return result
    for key in ("primary_color", "secondary_color"):
        if key in theme:
            value = str(theme[key]).strip()
            if not COLOR_RE.fullmatch(value):
                abort(400, description="Theme colors must be six-digit hexadecimal colors.")
            result[key] = value
    if "logo_url" in theme:
        value = str(theme["logo_url"]).strip()
        if value:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc:
                abort(400, description="Logo URLs must use HTTPS.")
        result["logo_url"] = value
    for key in ("tagline", "footer_text"):
        if key in theme:
            result[key] = str(theme[key]).strip()[:240]
    return result


def _page_payload(row):
    return {
        "id": row["id"], "slug": row["slug"], "title": row["title"],
        "content": row["content"], "status": row["status"],
        "is_home": bool(row["is_home"]),
        "show_in_navigation": bool(row["show_in_navigation"]),
        "navigation_order": row["navigation_order"],
    }


def _public_response(connection, website, page_slug):
    if not website or website["status"] != "published":
        abort(404)
    school = connection.execute(
        """SELECT name,description,city,county,region,country_code,
                  contact_email,contact_phone
           FROM schools
           WHERE id=? AND status='active' AND lifecycle_status='active'""",
        (website["school_id"],),
    ).fetchone()
    if not school:
        abort(404)
    page = connection.execute(
        """SELECT slug,title,content FROM school_website_pages
           WHERE website_id=? AND school_id=? AND slug=? AND status='published'""",
        (website["id"], website["school_id"], page_slug),
    ).fetchone()
    if not page:
        abort(404)
    nav = connection.execute(
        """SELECT slug,title FROM school_website_pages
           WHERE website_id=? AND school_id=? AND status='published'
             AND show_in_navigation=1
           ORDER BY navigation_order,title COLLATE NOCASE""",
        (website["id"], website["school_id"]),
    ).fetchall()
    theme = _clean_theme({}, website["theme_json"])
    # Jinja auto-escapes the page title, plain-text content and profile fields.
    return render_template_string(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ page.title }} | {{ school.name }}</title>
<meta name="description" content="{{ school.description or theme.tagline }}">
<style>
:root{--primary:{{ theme.primary_color }};--secondary:{{ theme.secondary_color }}}
*{box-sizing:border-box}body{margin:0;font:16px/1.65 system-ui,sans-serif;color:#243247;background:#f5f7fa}
header{background:var(--primary);color:white;padding:1.4rem max(5vw,1rem)}
header img{max-height:64px;max-width:180px;object-fit:contain}header h1{margin:.4rem 0}
nav{display:flex;flex-wrap:wrap;gap:1rem;padding:1rem max(5vw,1rem);background:white}
nav a{color:var(--primary);font-weight:600;text-decoration:none}
main{max-width:900px;margin:2rem auto;padding:2rem;background:white;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere}
footer{text-align:center;padding:2rem;color:#667085;border-top:3px solid var(--secondary)}
</style></head><body>
<header>{% if theme.logo_url %}<img src="{{ theme.logo_url }}" alt="{{ school.name }} logo">{% endif %}
<h1>{{ school.name }}</h1>{% if theme.tagline %}<p>{{ theme.tagline }}</p>{% endif %}</header>
<nav aria-label="Main navigation">{% for item in nav %}<a href="{{ url_for('school_websites.public_site_page', website_slug=website.slug, page_slug=item.slug) }}">{{ item.title }}</a>{% endfor %}</nav>
<main><h2>{{ page.title }}</h2><div>{{ page.content }}</div></main>
<footer>{{ theme.footer_text or school.name }}{% if school.city or school.country_code %} · {{ school.city or '' }} {{ school.country_code or '' }}{% endif %}
{% if school.contact_email %} · {{ school.contact_email }}{% endif %}</footer>
</body></html>""",
        website=website, school=school, page=page, nav=nav, theme=theme,
    )


def create_school_website_blueprint(connection_factory, require_school_context, is_platform_owner):
    bp = Blueprint("school_websites", __name__)

    @bp.get("/school-websites")
    @login_required
    def website_manager():
        school_id = _tenant_id(require_school_context)
        _permission("school.manage_websites", connection_factory, school_id)
        connection = connection_factory()
        try:
            rows = connection.execute(
                """SELECT w.id,w.name,w.slug,w.status,w.owner_user_id,w.updated_at,
                          COUNT(p.id) AS page_count
                   FROM school_websites w LEFT JOIN school_website_pages p
                     ON p.website_id=w.id AND p.school_id=w.school_id
                   WHERE w.school_id=? GROUP BY w.id ORDER BY w.created_at DESC""",
                (school_id,),
            ).fetchall()
            return jsonify({"websites": [dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.post("/api/school-websites")
    @login_required
    def create_website():
        school_id = _tenant_id(require_school_context)
        _permission("school.manage_websites", connection_factory, school_id)
        data = request.get_json(silent=True) or request.form
        name = str(data.get("name", "")).strip()[:120]
        slug = str(data.get("slug", "")).strip().lower()
        if not name:
            abort(400, description="Website name is required.")
        if not slug:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not SLUG_RE.fullmatch(slug) or slug in {"admin", "api", "login", "logout", "static", "school-websites"}:
            abort(400, description="Choose a valid, non-reserved website slug.")
        theme = _clean_theme(data.get("theme", {}))
        connection = connection_factory()
        try:
            cursor = connection.execute(
                """INSERT INTO school_websites
                   (school_id,name,slug,owner_user_id,theme_json,created_by,updated_by)
                   VALUES(?,?,?,?,?,?,?)""",
                (school_id, name, slug, current_user.id, json.dumps(theme),
                 current_user.id, current_user.id),
            )
            website_id = cursor.lastrowid
            connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,status,is_home,show_in_navigation,
                    created_by,updated_by)
                   VALUES(?,?, 'home', ?, 'draft',1,1,?,?)""",
                (website_id, school_id, name, current_user.id, current_user.id),
            )
            _audit(connection, school_id, website_id, "website.created", {"slug": slug})
            connection.commit()
            return jsonify({"id": website_id, "name": name, "slug": slug, "status": "draft"}), 201
        except Exception as exc:
            connection.rollback()
            if "UNIQUE" in str(exc).upper():
                abort(409, description="That website slug is already in use by this school.")
            raise
        finally:
            connection.close()

    @bp.get("/api/school-websites/<int:website_id>")
    @login_required
    def get_website(website_id):
        school_id = _tenant_id(require_school_context)
        connection = connection_factory()
        try:
            website = _website_row(connection, school_id, website_id)
            _authorize_website(connection_factory, website, school_id)
            pages = connection.execute(
                """SELECT * FROM school_website_pages
                   WHERE website_id=? AND school_id=? ORDER BY navigation_order,id""",
                (website_id, school_id),
            ).fetchall()
            domains = connection.execute(
                """SELECT id,hostname,status,verified_at FROM school_website_domains
                   WHERE website_id=? AND school_id=? ORDER BY id""",
                (website_id, school_id),
            ).fetchall()
            result = dict(website)
            result["theme"] = _json_object(result.pop("theme_json"))
            result["pages"] = [_page_payload(row) for row in pages]
            result["domains"] = [dict(row) for row in domains]
            return jsonify(result)
        finally:
            connection.close()

    @bp.patch("/api/school-websites/<int:website_id>")
    @login_required
    def update_website(website_id):
        school_id = _tenant_id(require_school_context)
        connection = connection_factory()
        try:
            website = _website_row(connection, school_id, website_id)
            _authorize_website(connection_factory, website, school_id)
            data = request.get_json(silent=True) or {}
            name = str(data.get("name", website["name"])).strip()[:120]
            if not name:
                abort(400, description="Website name is required.")
            theme = _clean_theme(data.get("theme", {}), website["theme_json"])
            owner_id = website["owner_user_id"]
            if "owner_user_id" in data:
                owner = connection.execute(
                    """SELECT u.id FROM users u WHERE u.id=? AND u.status='active'
                       AND (u.school_id=? OR EXISTS (
                           SELECT 1 FROM school_memberships m WHERE m.user_id=u.id
                             AND m.school_id=? AND m.status='active'))""",
                    (data["owner_user_id"], school_id, school_id),
                ).fetchone()
                if not owner:
                    abort(400, description="Website owner must be an active member of this school.")
                owner_id = owner["id"]
            connection.execute(
                """UPDATE school_websites SET name=?,theme_json=?,owner_user_id=?,
                   updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?""",
                (name, json.dumps(theme), owner_id, current_user.id, website_id, school_id),
            )
            _audit(connection, school_id, website_id, "website.updated", {"owner_user_id": owner_id})
            connection.commit()
            return jsonify({"id": website_id, "name": name, "owner_user_id": owner_id, "theme": theme})
        finally:
            connection.close()

    @bp.post("/api/school-websites/<int:website_id>/pages")
    @login_required
    def create_page(website_id):
        school_id = _tenant_id(require_school_context)
        connection = connection_factory()
        try:
            website = _website_row(connection, school_id, website_id)
            _authorize_website(connection_factory, website, school_id)
            data = request.get_json(silent=True) or {}
            title = str(data.get("title", "")).strip()[:160]
            slug = str(data.get("slug", "")).strip().lower()
            if not title:
                abort(400, description="Page title is required.")
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            if not SLUG_RE.fullmatch(slug) or slug in {"api", "static", "school-websites"}:
                abort(400, description="Choose a valid page slug.")
            content = str(data.get("content", ""))[:30000]
            show = bool(data.get("show_in_navigation", True))
            order = max(0, min(int(data.get("navigation_order", 0)), 10000))
            cursor = connection.execute(
                """INSERT INTO school_website_pages
                   (website_id,school_id,slug,title,content,status,is_home,
                    show_in_navigation,navigation_order,created_by,updated_by)
                   VALUES(?,?,?,?,?,'draft',0,?,?,?,?)""",
                (website_id, school_id, slug, title, content, int(show), order,
                 current_user.id, current_user.id),
            )
            _audit(connection, school_id, website_id, "page.created", {"slug": slug})
            connection.commit()
            return jsonify({"id": cursor.lastrowid, "slug": slug, "status": "draft"}), 201
        except Exception as exc:
            connection.rollback()
            if "UNIQUE" in str(exc).upper():
                abort(409, description="A page with that slug already exists.")
            raise
        finally:
            connection.close()

    @bp.patch("/api/school-websites/<int:website_id>/pages/<int:page_id>")
    @login_required
    def update_page(website_id, page_id):
        school_id = _tenant_id(require_school_context)
        connection = connection_factory()
        try:
            website = _website_row(connection, school_id, website_id)
            _authorize_website(connection_factory, website, school_id)
            page = connection.execute(
                """SELECT * FROM school_website_pages
                   WHERE id=? AND website_id=? AND school_id=?""",
                (page_id, website_id, school_id),
            ).fetchone()
            if not page:
                abort(404)
            data = request.get_json(silent=True) or {}
            status = str(data.get("status", page["status"]))
            if status not in PAGE_STATES:
                abort(400, description="Invalid page publication status.")
            if status == "published":
                _permission("school.publish_websites", connection_factory, school_id)
                if website["status"] != "published":
                    abort(409, description="Publish the website after preparing its home page.")
            title = str(data.get("title", page["title"])).strip()[:160]
            slug = str(data.get("slug", page["slug"])).strip().lower()
            if not title or not SLUG_RE.fullmatch(slug):
                abort(400, description="Page title and slug are required.")
            content = str(data.get("content", page["content"]))[:30000]
            show = int(bool(data.get("show_in_navigation", page["show_in_navigation"])))
            order = max(0, min(int(data.get("navigation_order", page["navigation_order"])), 10000))
            connection.execute(
                """UPDATE school_website_pages SET title=?,slug=?,content=?,status=?,
                   show_in_navigation=?,navigation_order=?,updated_by=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND website_id=? AND school_id=?""",
                (title, slug, content, status, show, order, current_user.id,
                 page_id, website_id, school_id),
            )
            _audit(connection, school_id, website_id, "page.updated", {"page_id": page_id, "status": status})
            connection.commit()
            return jsonify({"id": page_id, "slug": slug, "status": status})
        except Exception as exc:
            connection.rollback()
            if "UNIQUE" in str(exc).upper():
                abort(409, description="A page with that slug already exists.")
            raise
        finally:
            connection.close()

    @bp.post("/api/school-websites/<int:website_id>/publish")
    @login_required
    def publish_website(website_id):
        school_id = _tenant_id(require_school_context)
        _permission("school.publish_websites", connection_factory, school_id)
        connection = connection_factory()
        try:
            website = _website_row(connection, school_id, website_id)
            home = connection.execute(
                """SELECT id FROM school_website_pages
                   WHERE website_id=? AND school_id=? AND is_home=1
                     AND status='published'""",
                (website_id, school_id),
            ).fetchone()
            if not home:
                abort(409, description="Publish the home page before publishing this website.")
            connection.execute(
                """UPDATE school_websites SET status='published',published_at=CURRENT_TIMESTAMP,
                   updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?""",
                (current_user.id, website_id, school_id),
            )
            _audit(connection, school_id, website_id, "website.published")
            connection.commit()
            return jsonify({"id": website_id, "status": "published"})
        finally:
            connection.close()

    @bp.post("/api/school-websites/<int:website_id>/unpublish")
    @login_required
    def unpublish_website(website_id):
        school_id = _tenant_id(require_school_context)
        _permission("school.publish_websites", connection_factory, school_id)
        connection = connection_factory()
        try:
            _website_row(connection, school_id, website_id)
            connection.execute(
                """UPDATE school_websites SET status='draft',updated_by=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?""",
                (current_user.id, website_id, school_id),
            )
            _audit(connection, school_id, website_id, "website.unpublished")
            connection.commit()
            return jsonify({"id": website_id, "status": "draft"})
        finally:
            connection.close()

    @bp.post("/api/school-websites/<int:website_id>/domains")
    @login_required
    def add_domain(website_id):
        school_id = _tenant_id(require_school_context)
        connection = connection_factory()
        try:
            website = _website_row(connection, school_id, website_id)
            _authorize_website(connection_factory, website, school_id)
            data = request.get_json(silent=True) or {}
            hostname = str(data.get("hostname", "")).strip().lower().rstrip(".")
            if not HOST_RE.fullmatch(hostname):
                abort(400, description="Enter a valid fully qualified domain name.")
            token = "school-site-verification=" + secrets.token_urlsafe(24)
            try:
                cursor = connection.execute(
                    """INSERT INTO school_website_domains
                       (website_id,school_id,hostname,verification_token)
                       VALUES(?,?,?,?)""",
                    (website_id, school_id, hostname, token),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    abort(409, description="That domain is already registered.")
                raise
            _audit(connection, school_id, website_id, "domain.added", {"hostname": hostname})
            connection.commit()
            return jsonify({"id": cursor.lastrowid, "hostname": hostname,
                            "status": "pending", "dns_txt_value": token}), 201
        finally:
            connection.close()

    @bp.post("/api/school-websites/domains/<int:domain_id>/verify")
    @login_required
    def verify_domain(domain_id):
        if not is_platform_owner():
            abort(403)
        connection = connection_factory()
        try:
            domain = connection.execute(
                "SELECT * FROM school_website_domains WHERE id=?", (domain_id,)
            ).fetchone()
            if not domain:
                abort(404)
            connection.execute(
                """UPDATE school_website_domains SET status='verified',
                   verified_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'""",
                (domain_id,),
            )
            _audit(connection, domain["school_id"], domain["website_id"],
                   "domain.verified", {"hostname": domain["hostname"]})
            connection.commit()
            return jsonify({"id": domain_id, "status": "verified"})
        finally:
            connection.close()

    @bp.get("/school-sites/<website_slug>/")
    @bp.get("/school-sites/<website_slug>")
    @bp.get("/school-sites/<website_slug>/<page_slug>")
    def public_site_page(website_slug, page_slug="home"):
        connection = connection_factory()
        try:
            website = connection.execute(
                """SELECT * FROM school_websites
                   WHERE slug=? AND status='published'""",
                (website_slug,),
            ).fetchone()
            return _public_response(connection, website, page_slug)
        finally:
            connection.close()

    @bp.before_app_request
    def resolve_verified_custom_domain():
        """Resolve verified custom hosts before ordinary app routes, fail closed."""
        host = (request.host or "").split(":", 1)[0].lower().rstrip(".")
        if not host or host in {"localhost", "127.0.0.1", "testserver"}:
            return None
        connection = connection_factory()
        try:
            domain = connection.execute(
                """SELECT d.website_id,d.school_id,w.slug
                   FROM school_website_domains d
                   JOIN school_websites w ON w.id=d.website_id AND w.school_id=d.school_id
                   JOIN schools s ON s.id=d.school_id
                   WHERE d.hostname=? AND d.status='verified'
                     AND w.status='published' AND s.status='active'
                     AND s.lifecycle_status='active'""",
                (host,),
            ).fetchone()
            if not domain:
                return None
            path = request.path.strip("/")
            page_slug = path or "home"
            website = connection.execute(
                """SELECT * FROM school_websites
                   WHERE id=? AND school_id=? AND status='published'""",
                (domain["website_id"], domain["school_id"]),
            ).fetchone()
            return _public_response(connection, website, page_slug)
        finally:
            connection.close()

    return bp
