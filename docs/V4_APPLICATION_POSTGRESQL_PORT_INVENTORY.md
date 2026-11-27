# Version 4 application PostgreSQL port inventory

## Scope and safety

This inventory is based on the current `feature/v4-postgresql-compatibility` branch, including `app.py` (3,323 lines) and the staged database modules. It is a planning artifact only: it does not enable PostgreSQL in the Flask application, run migrations, change deployment configuration, or touch production data.

## Current verified boundary

- `database.py` intentionally rejects PostgreSQL selection with a fail-closed configuration error.
- `app.py:get_db_connection()` still returns a native SQLite connection through `connect_sqlite(DATABASE)`.
- `db.connection.DatabaseConnection` and `db.postgresql.connect_postgresql` are isolated primitives; the Flask application does not use them yet.
- The PostgreSQL migration module is an isolated fresh-install foundation, not a conversion/import of the existing SQLite schema or data.
- Existing CI evidence covers the adapter and migration primitives against SQLite and a disposable PostgreSQL 17 service. It does not prove the Flask routes/authentication/business workflows run on PostgreSQL.

## Application coupling found in app.py

A static scan of the current 3,323-line module found:

| Surface | Occurrences | Porting concern |
| --- | ---: | --- |
| `get_db_connection()` call sites | 33 (including definition) | All route/service DB access must use one backend-aware seam. |
| `PRAGMA` statements | 13 | SQLite-only schema inspection, FK toggles and FK checks. |
| `sqlite_master` references | 2 | SQLite-only migration introspection. |
| `AUTOINCREMENT` DDL | 17 | Requires PostgreSQL identity/sequence DDL. |
| `lastrowid` reads | 3 | Must use PostgreSQL `RETURNING` and a consistent ID contract. |
| `INSERT OR IGNORE` statements | 5 | Must become conflict-aware `ON CONFLICT` statements with equivalent uniqueness semantics. |
| `COLLATE NOCASE` uses | 17 | Requires a deliberate case-insensitive comparison/sort strategy; semantics must be tested. |
| `sqlite3.Error` / `sqlite3.IntegrityError` handling | multiple | Driver exceptions need a narrow, explicit translation boundary or backend-aware handling. |
| SQLite-specific date formatting | 3 | Replace with portable expressions or application-side values. |

The application also contains substantial SQLite DDL and data-preserving migration logic in its startup path. That logic includes table rebuilds, column/index introspection, foreign-key toggling, schema migration ledger writes and post-migration integrity checks. It must not be run against PostgreSQL through a superficial connection swap.

## Required implementation sequence

1. **Separate schema lifecycle from app startup.** Keep SQLite migrations intact. Introduce explicit backend-specific migration runners and a startup policy that refuses PostgreSQL until its schema is complete.
2. **Complete PostgreSQL schema parity.** Inventory every SQLite table, column, constraint, index, trigger and migration version. Add ordered PostgreSQL migrations for the full CMHS schema, not only the current core subset.
3. **Define connection and result contracts.** Standardize execute/executemany, row access, generated IDs, transactions, cursor lifecycle and exception mapping. Keep SQL translation limited to reviewed statements.
4. **Port SQL by workflow.** Authentication/account approval; school and student records; teacher assignments; grades/report cards; attendance; finance; homework; audit and administration. Replace dialect-specific constructs explicitly.
5. **Prove data preservation.** Build a disposable SQLite fixture from a sanitized schema/data fixture; test export/import mapping, IDs, password hashes, relationships, constraints, counts and rollback. Never use production as a test target.
6. **Run parity and isolation tests.** Exercise both backends through Flask test-client workflows, including role permissions, cross-school isolation, CSRF/authentication, concurrent writes and failure rollback.
7. **Enable only after gates pass.** PostgreSQL remains disabled in app configuration until schema parity, route coverage, migration/recovery evidence and deployment readiness are reviewed.

## Acceptance gates

- Existing SQLite suite and migration behavior remain green.
- Fresh PostgreSQL install reaches the complete application schema.
- A sanitized legacy SQLite fixture can be migrated without losing users, password hashes, students, grades, attendance, finance or audit history.
- Critical Flask workflows pass against both backends.
- Generated IDs, uniqueness, case-insensitive behavior, timestamps and transaction rollback match documented contracts.
- Tenant/school isolation and authorization tests pass on PostgreSQL.
- No application startup path silently falls back to SQLite.
- Production cutover is a separate, explicitly approved operation with backup and rollback plan.

## Current decision

Do not wire PostgreSQL into `app.py` yet. The current primitives are useful groundwork, but a connection-factory switch would route SQLite-specific startup migrations and query semantics into PostgreSQL and risk application failure or incorrect data behavior. Continue with schema parity and backend-specific migration separation first.
