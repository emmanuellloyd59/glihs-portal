# V4 PostgreSQL Migration Foundation

**Status:** Initial migration framework and fresh-install core schema implemented; integration verification pending.
**Branch:** `feature/v4-postgresql-compatibility`
**Application enablement:** Disabled. The Flask application remains SQLite-only.

## What was added

- `db/migrations.py` defines ordered, named PostgreSQL migrations with SHA-256 checksums.
- A PostgreSQL-only `schema_migrations` ledger records version, name, checksum and application time.
- A transaction-scoped PostgreSQL advisory lock serializes concurrent migration runners.
- The runner rejects gaps, unknown versions, changed migration names/checksums and unexpected ledger layouts.
- Each migration's DDL and ledger insert commit together; failures roll back.
- Migration 1 creates the fresh-install core school schema using PostgreSQL-native identity columns, timestamp/date/time types, numeric financial values, constraints, foreign keys and indexes.
- `tests/test_postgresql_migrations.py` exercises fresh installation, replay, generated IDs, ledger tampering, unknown history and DDL rollback against the disposable PostgreSQL 17 CI service.

## Explicit limitations

This is a migration foundation, not a complete PostgreSQL application port.

- The current migration is for a fresh PostgreSQL schema. It does not import or transform an existing SQLite database.
- Database-level tenant ownership triggers/guards and the full operational schema still need dedicated reviewed migrations.
- Existing SQLite migration functions remain in `app.py` and have not been rewritten or replaced.
- The Flask application does not call this runner, and `DATABASE_BACKEND=postgresql` remains rejected.
- Query conversion, generated-ID call sites, exception handling, date/case semantics, authenticated workflows, backup/restore and concurrency tests remain release blockers.
- Neon remains empty and untouched; production service and database remain untouched.

## Verification gate

The new PostgreSQL migration tests must pass on both Python 3.11 and 3.14 with the disposable PostgreSQL 17 CI service. A passing migration test suite validates only this isolated migration foundation. It does not authorize enabling PostgreSQL or merging/deploying the application.
