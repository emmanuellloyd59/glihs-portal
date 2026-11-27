"""Tenant-scoped School Configuration Centre.

Configuration is stored as versioned JSON documents with relational ownership
keys. Existing schools.name/contact/timezone/country fields are migrated into
the initial document without replacing legacy school or campus records.
"""
import copy
import json
import re
import sqlite3
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required


CONFIG_VERSION = 1
MODULES = {
    "school_website", "school_management", "learning_management",
    "education_finance", "communication", "analytics_ai", "education_marketplace",
}
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
CURRENCY_CODES = set("""AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF
BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF
DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL
HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD
KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN
MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB
TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD
XOF XPF YER ZAR ZMW ZWL""".split())
TOP_LEVEL_KEYS = {
    "branding", "contact", "academic_calendar", "grading_system",
    "regional", "modules", "operational_policies",
}
CAMPUS_OVERRIDE_KEYS = TOP_LEVEL_KEYS - {"modules"}


def _defaults(school):
    return {
        "branding": {
            "display_name": school["name"] or "",
            "logo_url": "",
            "primary_color": "#1769AA",
            "secondary_color": "#16A6A1",
        },
        "contact": {
            "email": school["contact_email"] or "",
            "phone": school["contact_phone"] or "",
            "website_url": school["website_url"] or "",
            "address_line1": school["address_line1"] or "",
            "address_line2": school["address_line2"] or "",
            "city": school["city"] or "",
            "region": school["region"] or "",
            "county": school["county"] or "",
            "postal_code": school["postal_code"] or "",
            "country_code": (school["country_code"] or "").upper(),
        },
        "academic_calendar": {
            "academic_year_label": "",
            "year_start": "",
            "year_end": "",
            "week_starts_on": "monday",
            "terms": [],
        },
        "grading_system": {
            "name": "Percentage",
            "passing_score": 50,
            "grades": [
                {"label": "A", "minimum": 80, "maximum": 100},
                {"label": "B", "minimum": 70, "maximum": 79.99},
                {"label": "C", "minimum": 60, "maximum": 69.99},
                {"label": "D", "minimum": 50, "maximum": 59.99},
                {"label": "F", "minimum": 0, "maximum": 49.99},
            ],
        },
        "regional": {
            "currency": "USD",
            "timezone": school["timezone"] or "UTC",
            "default_language": "en",
            "supported_languages": ["en"],
        },
        "modules": {"enabled": ["school_management"]},
        "operational_policies": {
            "attendance_required": True,
            "guardian_access_enabled": True,
            "student_self_service_enabled": False,
            "late_enrollment_requires_approval": True,
        },
    }


def _deep_merge(base, patch):
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_config(config, campus=False):
    errors = []
    if not isinstance(config, dict):
        return ["Configuration must be a JSON object."]
    allowed = CAMPUS_OVERRIDE_KEYS if campus else TOP_LEVEL_KEYS
    unknown = set(config) - allowed
    if unknown:
        errors.append("Unsupported configuration sections: " + ", ".join(sorted(unknown)))
    allowed_fields = {
        "branding": {"display_name", "logo_url", "primary_color", "secondary_color"},
        "contact": {"email", "phone", "website_url", "address_line1", "address_line2",
                    "city", "region", "county", "postal_code", "country_code"},
        "academic_calendar": {"academic_year_label", "year_start", "year_end",
                              "week_starts_on", "terms"},
        "grading_system": {"name", "passing_score", "grades"},
        "regional": {"currency", "timezone", "default_language", "supported_languages"},
        "modules": {"enabled"},
    }
    for section, allowed_keys in allowed_fields.items():
        value = config.get(section, {})
        if isinstance(value, dict):
            extra = set(value) - allowed_keys
            if extra:
                errors.append("Unsupported " + section.replace("_", " ") +
                              " fields: " + ", ".join(sorted(extra)))
    branding = config.get("branding", {})
    if not isinstance(branding, dict):
        errors.append("Branding must be an object.")
    else:
        if "display_name" in branding and (not isinstance(branding["display_name"], str)
                                            or not 2 <= len(branding["display_name"].strip()) <= 160):
            errors.append("Display name must contain 2 to 160 characters.")
        if "logo_url" in branding:
            value = branding["logo_url"]
            if not isinstance(value, str) or len(value) > 1000 or (
                value and not (value.startswith("https://") or
                               (value.startswith("/static/") and not value.startswith("//")))
            ):
                errors.append("Logo must use HTTPS or a local /static/ path.")
        for key in ("primary_color", "secondary_color"):
            if key in branding and (not isinstance(branding[key], str) or
                                    not COLOR_RE.fullmatch(branding[key])):
                errors.append(key.replace("_", " ").title() + " must be a six-digit hexadecimal color.")
    contact = config.get("contact", {})
    if not isinstance(contact, dict):
        errors.append("Contact information must be an object.")
    else:
        if "email" in contact and contact["email"] and not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", str(contact["email"])
        ):
            errors.append("Enter a valid institutional contact email.")
        if "website_url" in contact and contact["website_url"] and not re.fullmatch(
            r"https://[^\s]+", str(contact["website_url"]), re.I
        ):
            errors.append("Institution website must use HTTPS.")
        for key in ("phone", "address_line1", "address_line2", "city", "region", "county", "postal_code"):
            if key in contact and (not isinstance(contact[key], str) or len(contact[key]) > 255):
                errors.append("Invalid contact field: " + key)
        if "country_code" in contact and contact["country_code"] and not re.fullmatch(
            r"[A-Z]{2}", str(contact["country_code"])
        ):
            errors.append("Country code must be a two-letter ISO code.")
    calendar = config.get("academic_calendar", {})
    if not isinstance(calendar, dict):
        errors.append("Academic calendar must be an object.")
    else:
        if calendar.get("week_starts_on", "monday") not in {
            "monday", "sunday", "saturday"
        }:
            errors.append("Week start must be Monday, Sunday or Saturday.")
        parsed_year = {}
        for key in ("year_start", "year_end"):
            value = calendar.get(key, "")
            if value:
                try:
                    parsed_year[key] = date.fromisoformat(value)
                except (TypeError, ValueError):
                    errors.append(key.replace("_", " ").title() + " must be an ISO date (YYYY-MM-DD).")
        if len(parsed_year) == 2 and parsed_year["year_start"] > parsed_year["year_end"]:
            errors.append("Academic year start date must not follow its end date.")
        terms = calendar.get("terms", [])
        if not isinstance(terms, list) or len(terms) > 12:
            errors.append("Academic calendar may contain at most 12 terms.")
        else:
            seen = set()
            for term in terms:
                if not isinstance(term, dict):
                    errors.append("Each academic term must be an object.")
                    continue
                name = str(term.get("name", "")).strip()
                if not name or len(name) > 80 or name.lower() in seen:
                    errors.append("Academic term names must be unique and non-empty.")
                seen.add(name.lower())
                try:
                    start, end = date.fromisoformat(term["start"]), date.fromisoformat(term["end"])
                    if start > end:
                        errors.append("Academic term start date must not follow its end date.")
                except (KeyError, TypeError, ValueError):
                    errors.append("Each term requires valid start and end dates.")
    grading = config.get("grading_system", {})
    if not isinstance(grading, dict):
        errors.append("Grading system must be an object.")
    else:
        try:
            passing = float(grading.get("passing_score", 50))
            if not 0 <= passing <= 100:
                errors.append("Passing score must be between 0 and 100.")
        except (TypeError, ValueError):
            errors.append("Passing score must be numeric.")
        grades = grading.get("grades", [])
        if not isinstance(grades, list) or len(grades) > 30:
            errors.append("Grading system may contain at most 30 grade bands.")
        else:
            labels = set()
            numeric_bands = []
            for band in grades:
                if not isinstance(band, dict):
                    errors.append("Each grade band must be an object.")
                    continue
                label = str(band.get("label", "")).strip()
                try:
                    low, high = float(band["minimum"]), float(band["maximum"])
                    if not label or label.lower() in labels or low < 0 or high > 100 or low > high:
                        errors.append("Grade bands require unique labels and valid 0–100 ranges.")
                    labels.add(label.lower())
                    numeric_bands.append((low, high))
                except (KeyError, TypeError, ValueError):
                    errors.append("Each grade band requires numeric minimum and maximum values.")
            ordered_bands = sorted(numeric_bands)
            if any(ordered_bands[index][0] <= ordered_bands[index - 1][1]
                   for index in range(1, len(ordered_bands))):
                errors.append("Grading bands must not overlap.")
    regional = config.get("regional", {})
    if not isinstance(regional, dict):
        errors.append("Regional preferences must be an object.")
    else:
        currency = regional.get("currency", "USD")
        if not isinstance(currency, str) or not CURRENCY_RE.fullmatch(currency) or currency not in CURRENCY_CODES:
            errors.append("Currency must be a three-letter uppercase ISO 4217 code.")
        timezone_name = regional.get("timezone", "UTC")
        try:
            ZoneInfo(timezone_name)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            errors.append("Choose a valid IANA time zone.")
        default_language = regional.get("default_language", "en")
        languages = regional.get("supported_languages", [default_language])
        if not isinstance(default_language, str) or not LANGUAGE_RE.fullmatch(default_language):
            errors.append("Default language must use a valid language tag.")
        if not isinstance(languages, list) or not 1 <= len(languages) <= 20 or any(
            not isinstance(lang, str) or not LANGUAGE_RE.fullmatch(lang) for lang in languages
        ):
            errors.append("Supported languages must contain 1–20 valid language tags.")
        elif default_language not in languages:
            errors.append("Default language must be included in supported languages.")
    modules = config.get("modules", {})
    if not isinstance(modules, dict):
        errors.append("Module settings must be an object.")
    elif "enabled" in modules and (
        not isinstance(modules["enabled"], list) or
        len(set(modules["enabled"])) != len(modules["enabled"]) or
        any(module not in MODULES for module in modules["enabled"])
    ):
        errors.append("Choose supported, unique platform modules.")
    policies = config.get("operational_policies", {})
    if not isinstance(policies, dict) or len(policies) > 50:
        errors.append("Operational policies must be an object with at most 50 settings.")
    elif any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key)
             or not isinstance(value, (str, int, float, bool, list, dict, type(None)))
             for key, value in policies.items()):
        errors.append("Operational policy keys or values are invalid.")
    return errors


def _school_id_from_request():
    if current_user.is_authenticated and getattr(current_user, "role", None) == "super_admin" and bool(
        getattr(current_user, "is_super_admin", False)
    ) and getattr(current_user, "school_id", None) is None:
        selected = request.view_args.get("school_id") if request.view_args else None
        selected = selected or request.args.get("school_id", type=int)
        if selected:
            from authorization import require_permission
            require_permission("school.view_configuration", connection_factory, school_id=int(selected))
            return int(selected), "platform"
        abort(400, description="Platform owner must select a school.")
    if not current_user.is_authenticated:
        abort(401)
    school_id = getattr(current_user, "school_id", None)
    if school_id is None:
        abort(403)
    from authorization import require_permission
    require_permission("school.view_configuration", connection_factory, school_id=int(school_id))
    return int(school_id), "school"


def initialize_configuration_schema(connection_factory):
    connection = connection_factory()
    try:
        connection.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS school_configurations (
            school_id INTEGER PRIMARY KEY REFERENCES schools(id),
            version INTEGER NOT NULL DEFAULT 0,
            config_json TEXT NOT NULL,
            updated_by INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campus_configurations (
            school_id INTEGER NOT NULL,
            campus_id INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 0,
            overrides_json TEXT NOT NULL,
            updated_by INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (school_id, campus_id),
            FOREIGN KEY (school_id, campus_id) REFERENCES campuses(school_id, id)
        );
        CREATE TABLE IF NOT EXISTS school_configuration_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            campus_id INTEGER,
            version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            change_summary TEXT NOT NULL,
            changed_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (school_id, campus_id) REFERENCES campuses(school_id, id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_school_config_version
            ON school_configuration_versions(school_id, version) WHERE campus_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_campus_config_version
            ON school_configuration_versions(school_id, campus_id, version) WHERE campus_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_school_config_history
            ON school_configuration_versions(school_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS school_configuration_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id INTEGER NOT NULL REFERENCES schools(id),
            campus_id INTEGER,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            version INTEGER NOT NULL,
            changed_sections TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (school_id, campus_id) REFERENCES campuses(school_id, id)
        );
        CREATE INDEX IF NOT EXISTS idx_school_config_audit
            ON school_configuration_audit(school_id, created_at DESC);
        """)
        schools = connection.execute("SELECT * FROM schools").fetchall()
        for school in schools:
            exists = connection.execute(
                "SELECT 1 FROM school_configurations WHERE school_id=?", (school["id"],)
            ).fetchone()
            if exists:
                continue
            config = _defaults(school)
            # Preserve requested modules from verified onboarding applications where available.
            service_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_service_requests'"
            ).fetchone()
            if service_table:
                requested = connection.execute(
                    "SELECT service_key FROM school_service_requests WHERE school_id=? AND status IN ('requested','approved')",
                    (school["id"],),
                ).fetchall()
                enabled = {item["service_key"] for item in requested if item["service_key"] in MODULES}
                if enabled:
                    config["modules"]["enabled"] = sorted(set(config["modules"]["enabled"]) | enabled)
            encoded = json.dumps(config, sort_keys=True, separators=(",", ":"))
            connection.execute(
                """INSERT INTO school_configurations
                   (school_id, version, config_json, updated_by)
                   VALUES (?, 1, ?, NULL)""", (school["id"], encoded)
            )
            connection.execute(
                """INSERT INTO school_configuration_versions
                   (school_id, campus_id, version, snapshot_json, change_summary, changed_by)
                   VALUES (?, NULL, 1, ?, 'Initial legacy-settings preservation snapshot', NULL)""",
                (school["id"], encoded),
            )
            connection.execute(
                """INSERT INTO school_configuration_audit
                   (school_id, campus_id, actor_user_id, action, version, changed_sections)
                   VALUES (?, NULL, NULL, 'configuration.migrated', 1, ?)""",
                (school["id"], json.dumps(sorted(config.keys()))),
            )
        # Existing campus addresses and contact details remain canonical in campuses;
        # mirror them into versioned overrides so effective settings preserve their values.
        campuses = connection.execute("SELECT * FROM campuses ORDER BY school_id, id").fetchall()
        for campus in campuses:
            exists = connection.execute(
                "SELECT 1 FROM campus_configurations WHERE school_id=? AND campus_id=?",
                (campus["school_id"], campus["id"]),
            ).fetchone()
            if exists:
                continue
            overrides = {
                "branding": {"display_name": campus["name"]},
                "contact": {
                    "address_line1": campus["address_line1"] or "",
                    "address_line2": campus["address_line2"] or "",
                    "city": campus["city"] or "",
                    "region": campus["region"] or campus["county"] or "",
                    "postal_code": campus["postal_code"] or "",
                    "country_code": (campus["country_code"] or "").upper(),
                    "email": campus["contact_email"] or "",
                    "phone": campus["contact_phone"] or "",
                },
            }
            encoded = json.dumps(overrides, sort_keys=True, separators=(",", ":"))
            connection.execute(
                """INSERT INTO campus_configurations
                   (school_id, campus_id, version, overrides_json, updated_by)
                   VALUES (?, ?, 1, ?, NULL)""",
                (campus["school_id"], campus["id"], encoded),
            )
            connection.execute(
                """INSERT INTO school_configuration_versions
                   (school_id, campus_id, version, snapshot_json, change_summary, changed_by)
                   VALUES (?, ?, 1, ?, 'Initial legacy-campus-settings preservation snapshot', NULL)""",
                (campus["school_id"], campus["id"], encoded),
            )
            connection.execute(
                """INSERT INTO school_configuration_audit
                   (school_id, campus_id, actor_user_id, action, version, changed_sections)
                   VALUES (?, ?, NULL, 'campus_configuration.migrated', 1, ?)""",
                (campus["school_id"], campus["id"], json.dumps(sorted(overrides.keys()))),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _record_version(connection, school_id, campus_id, version, snapshot, summary, actor, changed_sections=None):
    connection.execute(
        """INSERT INTO school_configuration_versions
           (school_id, campus_id, version, snapshot_json, change_summary, changed_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (school_id, campus_id, version,
         json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
         summary[:240], actor),
    )
    connection.execute(
        """INSERT INTO school_configuration_audit
           (school_id, campus_id, actor_user_id, action, version, changed_sections)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (school_id, campus_id, actor,
         "configuration.updated" if campus_id is None else "campus_configuration.updated",
         version, json.dumps(sorted(changed_sections if changed_sections is not None else snapshot.keys()))),
    )


def create_school_configuration_blueprint(connection_factory):
    bp = Blueprint("school_configuration", __name__)

    @bp.app_context_processor
    def inject_school_branding():
        school_id = getattr(current_user, "school_id", None) if current_user.is_authenticated else None
        if (current_user.is_authenticated and getattr(current_user, "role", None) == "super_admin"
                and bool(getattr(current_user, "is_super_admin", False)) and school_id is None):
            selected = session.get("active_school_id")
            school_id = selected if isinstance(selected, int) and not isinstance(selected, bool) else None
        branding = {"logo_url": "", "primary_color": "#1769AA", "secondary_color": "#16A6A1"}
        if school_id is not None:
            connection = connection_factory()
            try:
                row = connection.execute(
                    "SELECT config_json FROM school_configurations WHERE school_id=?",
                    (school_id,),
                ).fetchone()
                if row:
                    branding.update(json.loads(row["config_json"]).get("branding", {}))
            except sqlite3.OperationalError:
                pass
            finally:
                connection.close()
        return {"current_institution_branding": branding}

    @bp.before_request
    def protect_api():
        if request.path.startswith("/api/v1/") and not current_user.is_authenticated:
            abort(401)

    def load_school(connection, school_id):
        school = connection.execute("SELECT * FROM schools WHERE id=?", (school_id,)).fetchone()
        if school is None:
            abort(404)
        if school["status"] != "active" or school["lifecycle_status"] != "active":
            abort(403)
        row = connection.execute(
            "SELECT version, config_json FROM school_configurations WHERE school_id=?", (school_id,)
        ).fetchone()
        if row is None:
            abort(500, description="School configuration baseline is missing.")
        return school, row, json.loads(row["config_json"])

    def get_context():
        school_id, access = _school_id_from_request()
        connection = connection_factory()
        try:
            school, row, config = load_school(connection, school_id)
            return school_id, access, school, row, config
        finally:
            connection.close()

    @bp.get("/school-network/configuration")
    @login_required
    def configuration_centre():
        school_id, access, school, row, config = get_context()
        connection = connection_factory()
        try:
            campuses = connection.execute(
                "SELECT id, code, name, status FROM campuses WHERE school_id=? ORDER BY name",
                (school_id,),
            ).fetchall()
        finally:
            connection.close()
        history = connection_factory()
        try:
            versions = history.execute(
                """SELECT version, change_summary, changed_by, created_at
                   FROM school_configuration_versions
                   WHERE school_id=? AND campus_id IS NULL ORDER BY version DESC LIMIT 20""",
                (school_id,),
            ).fetchall()
        finally:
            history.close()
        return render_template(
            "school_configuration/centre.html", school=school, school_id=school_id,
            access=access, config=config, version=row["version"], campuses=campuses,
            modules=sorted(MODULES), versions=versions,
        )

    @bp.post("/school-network/configuration")
    @login_required
    def update_configuration_form():
        school_id, access, school, row, current = get_context()
        from authorization import require_permission
        require_permission("school.manage_configuration", connection_factory, school_id=school_id)
        patch = {
            "branding": {
                "display_name": request.form.get("display_name", current["branding"]["display_name"]),
                "logo_url": request.form.get("logo_url", current["branding"]["logo_url"]),
                "primary_color": request.form.get("primary_color", current["branding"]["primary_color"]),
                "secondary_color": request.form.get("secondary_color", current["branding"]["secondary_color"]),
            },
            "contact": {
                "email": request.form.get("contact_email", current["contact"]["email"]),
                "phone": request.form.get("contact_phone", current["contact"]["phone"]),
                "website_url": request.form.get("website_url", current["contact"]["website_url"]),
                "address_line1": request.form.get("address_line1", current["contact"]["address_line1"]),
                "address_line2": request.form.get("address_line2", current["contact"]["address_line2"]),
                "city": request.form.get("city", current["contact"]["city"]),
                "region": request.form.get("region", current["contact"]["region"]),
                "county": request.form.get("county", current["contact"].get("county", "")),
                "postal_code": request.form.get("postal_code", current["contact"]["postal_code"]),
                "country_code": request.form.get("country_code", current["contact"]["country_code"]).upper(),
            },
            "regional": {
                "currency": request.form.get("currency", current["regional"]["currency"]).upper(),
                "timezone": request.form.get("timezone", current["regional"]["timezone"]),
                "default_language": request.form.get("default_language", current["regional"]["default_language"]),
                "supported_languages": [v.strip() for v in request.form.get("supported_languages", "").split(",") if v.strip()],
            },
            "modules": {"enabled": request.form.getlist("enabled_modules")},
        }
        for field, section in (
            ("academic_calendar_json", "academic_calendar"),
            ("grading_system_json", "grading_system"),
            ("operational_policies_json", "operational_policies"),
        ):
            try:
                patch[section] = json.loads(request.form.get(field, "{}"))
            except (TypeError, ValueError):
                flash(section.replace("_", " ").title() + " must be valid JSON.", "danger")
                suffix = "?school_id=" + str(school_id) if access == "platform" else ""
                return redirect(url_for("school_configuration.configuration_centre") + suffix)
        result = _save_school_config(connection_factory, school_id, patch, current_user.id)
        if not result[0]:
            for error in result[1]:
                flash(error, "danger")
        else:
            flash("School configuration saved as version " + str(result[2]) + ".", "success")
        suffix = "?school_id=" + str(school_id) if access == "platform" else ""
        return redirect(url_for("school_configuration.configuration_centre") + suffix)

    @bp.get("/api/v1/schools/<int:school_id>/configuration")
    @login_required
    def school_configuration_api(school_id):
        resolved, access = _school_id_from_request()
        if resolved != school_id:
            abort(404)
        connection = connection_factory()
        try:
            school, row, config = load_school(connection, school_id)
            return jsonify({"school_id": school_id, "version": row["version"], "configuration": config})
        finally:
            connection.close()

    @bp.patch("/api/v1/schools/<int:school_id>/configuration")
    @login_required
    def update_school_configuration_api(school_id):
        resolved, access = _school_id_from_request()
        from authorization import require_permission
        require_permission("school.manage_configuration", connection_factory, school_id=school_id)
        if resolved != school_id:
            abort(404)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "A JSON configuration object is required."}), 400
        patch = payload.get("configuration", payload)
        if not isinstance(patch, dict):
            return jsonify({"error": "Configuration must be an object."}), 400
        expected_version = payload.get("expected_version") if "configuration" in payload else None
        ok, errors, version = _save_school_config(
            connection_factory, school_id, patch, current_user.id,
            expected_version=expected_version,
        )
        if not ok:
            return jsonify({"errors": errors}), 409 if errors == ["Configuration changed since it was loaded. Refresh before saving."] else 422
        return jsonify({"school_id": school_id, "version": version, "status": "saved"})

    @bp.get("/api/v1/schools/<int:school_id>/configuration/history")
    @login_required
    def school_configuration_history(school_id):
        resolved, _ = _school_id_from_request()
        if resolved != school_id:
            abort(404)
        connection = connection_factory()
        try:
            load_school(connection, school_id)
            rows = connection.execute(
                """SELECT id, version, change_summary, changed_by, created_at
                   FROM school_configuration_versions
                   WHERE school_id=? AND campus_id IS NULL ORDER BY version DESC LIMIT 100""",
                (school_id,),
            ).fetchall()
            return jsonify({"data": [dict(row) for row in rows]})
        finally:
            connection.close()

    @bp.get("/api/v1/schools/<int:school_id>/campuses/<int:campus_id>/configuration")
    @login_required
    def campus_configuration_api(school_id, campus_id):
        resolved, _ = _school_id_from_request()
        if resolved != school_id:
            abort(404)
        connection = connection_factory()
        try:
            load_school(connection, school_id)
            campus = connection.execute(
                "SELECT id FROM campuses WHERE id=? AND school_id=?", (campus_id, school_id)
            ).fetchone()
            if campus is None:
                abort(404)
            school_row = connection.execute(
                "SELECT version, config_json FROM school_configurations WHERE school_id=?", (school_id,)
            ).fetchone()
            override = connection.execute(
                "SELECT version, overrides_json FROM campus_configurations WHERE school_id=? AND campus_id=?",
                (school_id, campus_id),
            ).fetchone()
            overrides = json.loads(override["overrides_json"]) if override else {}
            effective = _deep_merge(json.loads(school_row["config_json"]), overrides)
            return jsonify({
                "school_id": school_id, "campus_id": campus_id,
                "school_version": school_row["version"],
                "campus_version": override["version"] if override else 0,
                "overrides": overrides, "effective_configuration": effective,
            })
        finally:
            connection.close()

    @bp.patch("/api/v1/schools/<int:school_id>/campuses/<int:campus_id>/configuration")
    @login_required
    def update_campus_configuration_api(school_id, campus_id):
        resolved, _ = _school_id_from_request()
        from authorization import require_permission
        require_permission("school.manage_configuration", connection_factory,
                           school_id=school_id, campus_id=campus_id)
        if resolved != school_id:
            abort(404)
        payload = request.get_json(silent=True)
        patch = payload.get("overrides") if isinstance(payload, dict) else None
        if not isinstance(patch, dict):
            return jsonify({"error": "A campus overrides object is required."}), 400
        connection = connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            load_school(connection, school_id)
            if not connection.execute(
                "SELECT 1 FROM campuses WHERE id=? AND school_id=?", (campus_id, school_id)
            ).fetchone():
                abort(404)
            row = connection.execute(
                "SELECT version, overrides_json FROM campus_configurations WHERE school_id=? AND campus_id=?",
                (school_id, campus_id),
            ).fetchone()
            current = json.loads(row["overrides_json"]) if row else {}
            if len(json.dumps(patch)) > 65536:
                connection.rollback()
                return jsonify({"errors": ["Campus configuration payload exceeds 64 KB."]}), 413
            merged = _deep_merge(current, patch)
            errors = _validate_config(merged, campus=True)
            if errors:
                connection.rollback()
                return jsonify({"errors": errors}), 422
            expected = payload.get("expected_version")
            current_version = row["version"] if row else 0
            if expected is not None and expected != current_version:
                connection.rollback()
                return jsonify({"errors": ["Campus configuration changed since it was loaded."]}), 409
            version = current_version + 1
            encoded = json.dumps(merged, sort_keys=True, separators=(",", ":"))
            connection.execute(
                """INSERT INTO campus_configurations
                   (school_id, campus_id, version, overrides_json, updated_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(school_id, campus_id) DO UPDATE SET
                     version=excluded.version, overrides_json=excluded.overrides_json,
                     updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP""",
                (school_id, campus_id, version, encoded, current_user.id),
            )
            # Keep the legacy campus directory aligned with its effective campus identity/contact values.
            branding = merged.get("branding", {})
            contact = merged.get("contact", {})
            if "display_name" in branding:
                connection.execute(
                    "UPDATE campuses SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?",
                    (branding["display_name"], campus_id, school_id),
                )
            if contact:
                connection.execute(
                    """UPDATE campuses SET address_line1=?, address_line2=?, city=?, county=?,
                       region=?, postal_code=?, country_code=?, contact_email=?, contact_phone=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=? AND school_id=?""",
                    (contact.get("address_line1", ""), contact.get("address_line2", ""),
                     contact.get("city", ""), contact.get("county", ""), contact.get("region", ""),
                     contact.get("postal_code", ""), contact.get("country_code", ""),
                     contact.get("email", ""), contact.get("phone", ""), campus_id, school_id),
                )
            changed_sections = [key for key in merged if current.get(key) != merged.get(key)]
            _record_version(connection, school_id, campus_id, version, merged,
                            "Campus configuration updated", current_user.id, changed_sections)
            connection.commit()
            return jsonify({"school_id": school_id, "campus_id": campus_id, "version": version, "status": "saved"})
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @bp.get("/api/v1/schools/<int:school_id>/campuses/<int:campus_id>/configuration/history")
    @login_required
    def campus_configuration_history(school_id, campus_id):
        resolved, _ = _school_id_from_request()
        if resolved != school_id:
            abort(404)
        connection = connection_factory()
        try:
            load_school(connection, school_id)
            if not connection.execute(
                "SELECT 1 FROM campuses WHERE id=? AND school_id=?", (campus_id, school_id)
            ).fetchone():
                abort(404)
            rows = connection.execute(
                """SELECT id, version, change_summary, changed_by, created_at
                   FROM school_configuration_versions
                   WHERE school_id=? AND campus_id=? ORDER BY version DESC LIMIT 100""",
                (school_id, campus_id),
            ).fetchall()
            return jsonify({"data": [dict(row) for row in rows]})
        finally:
            connection.close()

    return bp


def _save_school_config(connection_factory, school_id, patch, actor_id, expected_version=None):
    if not isinstance(patch, dict):
        return False, ["Configuration must be an object."], None
    connection = connection_factory()
    try:
        connection.execute("BEGIN IMMEDIATE")
        school = connection.execute("SELECT * FROM schools WHERE id=?", (school_id,)).fetchone()
        if school is None:
            connection.rollback()
            return False, ["School not found."], None
        if school["status"] != "active" or school["lifecycle_status"] != "active":
            connection.rollback()
            return False, ["School must be active to change configuration."], None
        row = connection.execute(
            "SELECT version, config_json FROM school_configurations WHERE school_id=?", (school_id,)
        ).fetchone()
        if row is None:
            connection.rollback()
            return False, ["School configuration has not been initialized."], None
        if expected_version is not None and expected_version != row["version"]:
            connection.rollback()
            return False, ["Configuration changed since it was loaded. Refresh before saving."], None
        if len(json.dumps(patch)) > 65536:
            connection.rollback()
            return False, ["Configuration payload exceeds 64 KB."], None
        current = json.loads(row["config_json"])
        merged = _deep_merge(current, patch)
        errors = _validate_config(merged)
        if errors:
            connection.rollback()
            return False, errors, None
        version = row["version"] + 1
        encoded = json.dumps(merged, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """UPDATE school_configurations SET version=?, config_json=?, updated_by=?,
               updated_at=CURRENT_TIMESTAMP WHERE school_id=?""",
            (version, encoded, actor_id, school_id),
        )
        # Keep established application-facing school fields synchronized.
        branding = merged["branding"]
        contact = merged["contact"]
        regional = merged["regional"]
        connection.execute(
            """UPDATE schools SET name=?, contact_email=?, contact_phone=?, website_url=?,
               address_line1=?, address_line2=?, city=?, county=?, region=?, postal_code=?,
               country_code=?, timezone=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (branding["display_name"], contact.get("email", ""), contact.get("phone", ""),
             contact.get("website_url", ""), contact.get("address_line1", ""),
             contact.get("address_line2", ""), contact.get("city", ""),
             contact.get("county", ""), contact.get("region", ""), contact.get("postal_code", ""),
             contact.get("country_code", ""), regional["timezone"], school_id),
        )
        changed_sections = [key for key in merged if current.get(key) != merged.get(key)]
        _record_version(connection, school_id, None, version, merged,
                        "School configuration updated", actor_id, changed_sections)
        connection.commit()
        return True, [], version
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
