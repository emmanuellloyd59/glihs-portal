# V4 PostgreSQL Migration Foundation

**Status:** Two PostgreSQL-native migrations implemented; PostgreSQL 17 integration verification pending.
**Branch:** `feature/v4-postgresql-compatibility`
**Application enablement:** Disabled. The Flask application remains SQLite-only.

## What was added

- `db/migrations.py` defines ordered, named PostgreSQL migrations with SHA-256 checksums.
- A PostgreSQL-only `schema_migrations` ledger records version, name, checksum and application time.
- A transaction-scoped PostgreSQL advisory lock serializes migration runners for the full migration batch.
- The runner rejects gaps, unknown versions, changed migration names/checksums and unexpected ledger layouts.
- All pending DDL and ledger entries in one invocation commit atomically; any failure rolls back the complete pending batch.
- Migration 1 creates a fresh-install core schema for schools, users, students, parent links, finance, grades, report cards, attendance, audit logs, timetables, teacher assignments and homework.
- Migration 2 adds PostgreSQL trigger guards that reject cross-school student, parent, teacher, staff-actor and homework relationships while allowing explicitly designated super-admin actions.
- The schema uses PostgreSQL-native identity columns, timestamp/date/time types, numeric financial values, constraints, foreign keys and indexes.
- `tests/test_postgresql_migrations.py` covers fresh install, replay, generated IDs, checksum/ledger tampering, cross-school relationship rejection, super-admin actor behavior, and transactional rollback against disposable PostgreSQL 17.

## Explicit limitations

This is a migration foundation, not a complete PostgreSQL application port.

- These migrations create a fresh PostgreSQL schema. They do not import or transform an existing SQLite database.
- The full operational schema and all application-specific indexes/constraints still require inventory and reviewed migrations.
- Existing SQLite migration functions remain in `app.py` and have not been rewritten or replaced.
- The Flask application does not call this runner, and `DATABASE_BACKEND=postgresql` remains rejected.
- Query conversion, generated-ID call sites, exception handling, date/case semantics, authenticated workflows, backup/restore and concurrency tests remain release blockers.
- Neon remains empty and untouched; production service and database remain untouched.

## Verification gate

The migration tests must pass on both Python 3.11 and 3.14 with the disposable PostgreSQL 17 CI service. Passing these tests validates only the isolated migration foundation. It does not authorize enabling PostgreSQL or merging/deploying the application.
