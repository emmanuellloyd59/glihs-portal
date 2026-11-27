# V4 PostgreSQL Port — Application Compatibility Inventory

**Status:** Inventory completed; application backend remains SQLite-only.  
**Branch:** `feature/v4-postgresql-compatibility`  
**Safety:** No production database, deployment, Neon schema, or school records were changed.

## Verified current boundary

The PostgreSQL connection primitives and DB-API facade exist, but the Flask application is intentionally not wired to them. `database.py` still rejects `DATABASE_BACKEND=postgresql`, and `app.py:get_db_connection()` still returns a SQLite connection. Keep this fail-closed behavior until all migration and query groups below have integration coverage.

The facade currently adapts positional qmark placeholders only. It does not translate SQLite DDL, catalog queries, triggers, date functions, conflict syntax, transaction modes, generated IDs, exception classes, or backup behavior.

## Compatibility inventory

| Area | Confirmed SQLite-specific dependencies | Required port work |
|---|---|---|
| `app.py` startup and core migrations | `sqlite_master`, `PRAGMA table_info`, `PRAGMA index_list`, `PRAGMA index_info`, `PRAGMA foreign_key_check`, `PRAGMA foreign_keys`, `BEGIN IMMEDIATE`, `AUTOINCREMENT`, SQLite rebuild-table migrations, `INSERT OR IGNORE`, triggers, `lastrowid` assumptions | Split schema inspection from migration logic; author PostgreSQL DDL/migrations; replace SQLite table-rebuild patterns; use explicit `RETURNING id`; validate constraints and migration ledger under real PG |
| `homework.py` | `AUTOINCREMENT`, SQLite trigger syntax and `RAISE(ABORT)`, `PRAGMA foreign_key_check`, `BEGIN IMMEDIATE`, `INSERT OR IGNORE` | Port tables, indexes and ownership guards to PostgreSQL constraints/triggers; preserve atomic migration and school isolation |
| `staff_attendance.py` | Positional qmark SQL is present; queries need dialect review | Verify all statements, date/time behavior, upserts, generated IDs, concurrency and row semantics against PG |
| `school_performance.py` | SQLite `date()` and `COLLATE NOCASE` appear in reporting queries | Replace with portable or PostgreSQL-compatible expressions; test date boundaries and case-insensitive ordering |
| `demo_staff.py` | Positional qmark SQL; insert operations | Audit generated-ID and conflict behavior; ensure demo setup is idempotent and isolated |
| `scripts/sqlite_backup.py` | SQLite URI read-only mode, `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, `sqlite_master` | Keep explicitly SQLite-only or add a separate PostgreSQL backup/restore implementation using PostgreSQL-native tools and validation |

## Required implementation sequence

1. **Core schema/migration layer:** introduce backend-specific migration modules and a versioned PostgreSQL baseline. Do not translate SQLite catalog SQL mechanically.
2. **Core app query layer:** port account, school, student, grade and finance operations. Preserve IDs and password hashes; test existing-data migration on disposable fixtures.
3. **Operational modules:** port homework, attendance, performance and demo workflows, including cross-school ownership guards.
4. **Recovery:** implement PostgreSQL-native backup/restore and integrity verification; retain the SQLite utility as a separate backend-specific tool.
5. **Integration gate:** run the complete test suite against both SQLite and PostgreSQL 17; add authenticated end-to-end workflows, migration replay/idempotency, rollback, concurrency, and backup/restore drills.
6. **Enablement:** only after gates pass, allow explicit PostgreSQL selection in configuration. Keep production and Neon untouched until separately authorized.

## Release blockers

- PostgreSQL is not yet a supported application backend.
- No application schema has been created in the Neon staging project.
- No existing school data has been migrated.
- No authenticated PostgreSQL application workflow has been demonstrated.
- No PostgreSQL backup/restore drill has passed.

A green adapter test suite is necessary but is not evidence that the complete application supports PostgreSQL.
