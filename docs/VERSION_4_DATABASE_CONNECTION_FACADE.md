# Staged DB-API connection facade

**Status:** Initial execution facade and unit contract tests added; not wired into Flask routes or enabled for PostgreSQL.  
**Scope:** V4 PostgreSQL compatibility workstream only.

## Contract implemented

`db/connection.py` introduces `DatabaseConnection`, a small facade that delegates the common DB-API lifecycle operations (`execute`, `executemany`, `commit`, `rollback`, `close`, `cursor`, context-manager operations) while exposing driver-specific attributes for compatibility during migration.

- SQLite SQL and parameters pass through unchanged.
- PostgreSQL positional qmark parameters are converted through the existing SQL-aware lexical converter.
- PostgreSQL `executemany` validates a consistent positional parameter count before dispatch.
- Unsupported named parameter mappings and empty PostgreSQL batch input fail explicitly rather than guessing.
- The facade does not translate DDL, catalog queries, triggers, transaction statements, generated-ID behavior, date functions, conflict syntax or driver exceptions.

## Deliberate non-goals / release blockers

This facade is not a completed PostgreSQL backend. It is not yet used by `app.py`, `homework.py`, `staff_attendance.py`, `school_performance.py` or operational scripts. PostgreSQL application startup remains fail-closed. In particular, the existing app still relies on SQLite-specific schema migrations, PRAGMA/catalog inspection, `lastrowid`, SQLite exception classes and transaction semantics. Those must be ported and tested as separate query/migration groups before this facade can be wired into production application paths.

## Verification

The companion tests cover SQLite pass-through and row/transaction behavior, PostgreSQL placeholder conversion, batch parameter validation, explicit rejection of unsupported parameter styles, and lifecycle delegation. A passing unit test suite for this facade does not establish end-to-end PostgreSQL compatibility; a real PostgreSQL integration suite and application workflow tests remain required.
