# Version 4 Database Backend Architecture and Compatibility Plan

**Status:** Architecture decision and source audit complete; implementation not yet started.  
**Target branch:** `feature/v4-postgresql-compatibility`  
**Base:** `version-4/reconciled-main-2026-11-26`  
**Production impact:** None. This document does not authorize a production database change, merge, or deployment.

## 1. Decision

Retain SQLite as a fully supported backend and add PostgreSQL as a second, explicitly selected backend using **Psycopg 3 (DB-API)** behind a small connection/row abstraction. Do not attempt a wholesale ORM conversion in this V4 stabilization pass.

The abstraction will normalize connection lifecycle, execution, parameter binding, row access and transaction operations. It will not pretend that SQLite SQL is portable. Schema DDL, migration operations, database catalog inspection, trigger definitions and genuinely dialect-specific queries will have explicit SQLite and PostgreSQL implementations.

Why this approach:
- The application already has a large, tested body of parameterized SQL and route logic. A wholesale ORM rewrite would change too much at once and make authorization regressions harder to isolate.
- Psycopg 3 provides a maintained PostgreSQL driver and standard transaction primitives while keeping query changes reviewable.
- The existing SQLite backend and 190-test regression suite remain the compatibility baseline.
- Dialect differences remain visible and testable instead of being hidden in broad string substitutions.

## 2. Verified source audit

The audit inspected the current PR #51 head across the application and operational scripts. The counts below are static source-occurrence counts, not a claim that every question mark is necessarily a bind parameter.

| File | `.execute()` | `.executemany()` | Raw `?` tokens | Notable SQLite-specific usage |
|---|---:|---:|---:|---|
| `app.py` | 219 | 2 | 389 | PRAGMA/catalog queries, table rebuilds, triggers, transaction/savepoint logic, generated IDs, SQLite exceptions |
| `homework.py` | 26 | 0 | 39 | schema creation, triggers, foreign-key checks, `INSERT OR IGNORE`, `BEGIN IMMEDIATE` |
| `staff_attendance.py` | 20 | 0 | 63 | parameterized SQL |
| `school_performance.py` | 8 | 0 | 13 | parameterized SQL |
| `demo_staff.py` | 6 | 0 | 23 | parameterized SQL |
| `scripts/sqlite_backup.py` | 6 | 0 | 2 | SQLite backup API, PRAGMA, sqlite_master, SQLite errors |
| `scripts/import_demo_students.py` | 6 | 1 | 8 | PRAGMA, explicit transaction, SQLite-oriented import |
| **Total** | **291** | **3** | **537** | |

Additional static findings across production source:
- 22 PRAGMA occurrences and 4 `sqlite_master` catalog references.
- 9 `BEGIN IMMEDIATE` statements and 5 explicit SAVEPOINT occurrences.
- 15 SQLite trigger definitions, including school-ownership and homework guards.
- 19 `AUTOINCREMENT` declarations.
- 6 `INSERT OR IGNORE` statements.
- 3 `lastrowid` assumptions.
- 4 `strftime` and 1 `printf` SQL function occurrences.
- SQLite-specific exception handling in the application and backup utility.
- The current schema migration code rebuilds tables and inspects SQLite index/catalog metadata. Those operations cannot be enabled for PostgreSQL by changing only the connection string.

The raw question-mark counts must be reclassified by a SQL-aware scanner before automated conversion. Question marks inside literals, comments or operators must never be rewritten as bind markers.

## 3. Target architecture

### 3.1 Public connection contract

Keep `get_db_connection()` as the application-facing entry point. Move backend selection and connection creation behind a stable contract that supports the operations actually used by the application:

- `execute(sql, params)` and `executemany(sql, parameter_sets)`
- `commit()`, `rollback()`, `close()`
- transaction-state inspection where required
- context-manager behavior where required
- consistent row access by both column name and ordinal index until call sites are deliberately normalized
- predictable, backend-neutral exception categories for integrity and operational failures

The SQLite implementation must preserve `sqlite3.Row`, foreign-key enforcement and current transaction behavior. The PostgreSQL implementation must use PostgreSQL-native foreign-key enforcement and Psycopg transaction semantics.

### 3.2 Module boundaries

Proposed layout (introduced incrementally; no bulk file move):

- `database.py`: configuration validation and public factory/facade.
- `db/sqlite_backend.py`: existing SQLite connection setup and SQLite-specific helpers.
- `db/postgres_backend.py`: Psycopg 3 connection creation, row factory and PostgreSQL helpers.
- `db/rows.py`: tested row compatibility while the application still uses both named and positional row access.
- `db/sql.py`: narrowly scoped SQL utilities, including a lexical qmark-to-Psycopg placeholder converter if needed. It must understand quoted strings, quoted identifiers, line/block comments and escaped question marks; it must not translate SQL dialect features.
- `db/migrations/sqlite/` and `db/migrations/postgres/`: explicit backend-specific schema and data migrations.

The application must not import Psycopg types throughout route modules. Driver-specific details stay inside the backend boundary.

### 3.3 Configuration and fail-closed selection

- Preserve the existing SQLite default for environments with no PostgreSQL configuration.
- Add an explicit backend setting, e.g. `DATABASE_BACKEND=sqlite|postgresql`.
- PostgreSQL selection requires a valid PostgreSQL `DATABASE_URL`; SQLite selection uses `DATABASE_PATH`.
- Reject contradictory settings, unsupported URL schemes, missing required values and unknown backend names at startup.
- Never silently fall back from PostgreSQL to SQLite.
- Do not log or expose database URLs, passwords or connection secrets.
- Do not configure the staging URL until the PostgreSQL backend and integration suite are ready.

### 3.4 Query and SQL dialect policy

Do not apply global text replacements to the application source. Convert query groups with tests and review each semantic difference.

| SQLite construct or assumption | PostgreSQL treatment |
|---|---|
| `?` positional markers | Psycopg parameter style after SQL-aware conversion or explicit query rewrite |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT GENERATED BY DEFAULT AS IDENTITY` (or a deliberately selected integer width) |
| `cursor.lastrowid` | `INSERT ... RETURNING id`; consume the returned identifier explicitly |
| `INSERT OR IGNORE` | `INSERT ... ON CONFLICT (...) DO NOTHING`, with the intended conflict target documented |
| `PRAGMA table_info/index_list/index_info` | PostgreSQL catalog / `information_schema` inspection behind backend-specific helpers |
| `sqlite_master` | PostgreSQL catalog queries; never emulate SQLite catalog tables |
| `PRAGMA foreign_key_check` | Explicit orphan/integrity queries plus PostgreSQL constraints |
| `PRAGMA foreign_keys` | PostgreSQL foreign keys are enforced by the server; no per-connection toggle |
| `BEGIN IMMEDIATE` | Explicit transaction plus targeted row locks or transaction-scoped advisory locks for migration coordination; never a blind text substitution |
| SQLite `RAISE(ABORT,...)` triggers | PostgreSQL trigger functions using PL/pgSQL and appropriate SQLSTATE/constraint errors |
| `COLLATE NOCASE` | Deliberate case-insensitive uniqueness/comparison strategy, such as normalized values or `citext`, with tests |
| `IS` / `IS NOT` null-safe comparisons | Review each expression and use `IS [NOT] DISTINCT FROM` where that is the intended semantics |
| `strftime` / `printf` | Native PostgreSQL date/time and formatting expressions or application-side formatting |
| SQLite table rebuild migrations | Separate PostgreSQL `ALTER TABLE` / data migration with explicit constraints and rollback plan |
| `sqlite3.IntegrityError` and related exceptions | Translate Psycopg SQLSTATE classes at the backend boundary; preserve specific conflict handling |

All query changes affecting school scope, teacher assignments, finance balances, report-card publication, parent/student links, homework ownership or audit records require focused authorization and tenant-isolation regression tests.

### 3.5 Transactions, locking and audit atomicity

- Preserve the invariant that each protected business mutation and its audit event commit or roll back together.
- Replace SQLite's database-wide writer-lock assumptions with targeted PostgreSQL row locks (for example, locking the relevant student finance row before calculating and recording a payment).
- Use transaction-scoped advisory locks only for narrowly defined schema-migration coordination, not as a blanket lock for ordinary requests.
- Define savepoint behavior and nested transaction ownership explicitly. A helper must not commit or roll back a transaction owned by its caller.
- Verify that errors leave connections in a usable or explicitly rolled-back state.
- Test concurrent payment recording, duplicate admission numbers, teacher assignment updates and migration startup races.

### 3.6 Schema, constraints and tenant ownership

- Preserve the existing migration ledger's fail-closed behavior and reject unknown, conflicting or checksum-mismatched migration records.
- Introduce versioned, ordered migrations with a clear distinction between fresh installation and upgrade from each supported legacy schema.
- Implement PostgreSQL DDL separately; do not feed SQLite DDL or trigger SQL to PostgreSQL.
- Prefer native composite unique constraints and composite foreign keys for same-school relationships where the data model permits them.
- Implement the remaining cross-table ownership rules as PostgreSQL constraint triggers or equivalent transactional constraints, with tests proving they reject mismatched school IDs.
- Keep foreign keys, uniqueness, check constraints and audit invariants enabled throughout migration. Do not disable integrity checks to make an upgrade pass.
- Preserve all user IDs, password hashes, student IDs, school relationships, finance balances and audit history during any supported upgrade.

## 4. Implementation sequence and acceptance criteria

### Stage A — connection contract (first implementation stage)
1. Add backend configuration parsing and validation without enabling PostgreSQL in deployed environments.
2. Formalize the connection/row/exception contract and preserve the existing SQLite factory.
3. Add tests for default SQLite selection, explicit SQLite selection, malformed PostgreSQL URLs, contradictory settings, unknown backend names and fail-closed behavior.
4. Add row compatibility tests for named and ordinal access, NULL values and duplicate column names.
5. Keep all current SQLite tests passing.

**Acceptance:** SQLite behavior is unchanged; invalid or unsupported PostgreSQL configuration fails at startup; no fallback is possible.

### Stage B — PostgreSQL connection and transaction primitives
1. Add and pin Psycopg 3.
2. Implement PostgreSQL connection creation, row factory and backend exception normalization.
3. Test commit, rollback, savepoints, connection closure, constraint errors and transaction-state recovery against a disposable PostgreSQL 17 CI service.
4. Test SQL placeholder conversion independently with literals, comments, quoted identifiers, escaped markers and mixed parameter counts.

**Acceptance:** backend primitives pass real PostgreSQL tests; application schema and routes are still not switched to PostgreSQL.

### Stage C — schema and migrations
1. Extract the current SQLite schema/migration operations into explicit SQLite migration modules without changing behavior.
2. Build PostgreSQL fresh-install migrations and supported upgrade paths from verified legacy schema fixtures.
3. Port schema indexes, partial indexes, uniqueness, checks, foreign keys and all tenant-ownership guards.
4. Add migration-ledger validation, migration locking, rollback and repeated-startup tests.
5. Verify before/after record counts and stable identifiers on synthetic fixtures.

**Acceptance:** fresh PostgreSQL initialization and every declared upgrade path are repeatable, transactional and preserve fixture data.

### Stage D — application query conversion
Migrate in bounded feature groups, retaining both-backend tests after each group:
1. Authentication, user/school context and session loading.
2. Student registration, student profiles and parent/student links.
3. Academic grades, attendance, teacher assignments and timetables.
4. Finance and payment ledger, including concurrency and audit atomicity.
5. Report cards and publication.
6. Announcements, learning materials and file metadata.
7. Homework and submissions.
8. Dashboard, school performance, staff operations, demo imports and administrative utilities.

**Acceptance:** every group passes existing role/tenant tests on SQLite and equivalent PostgreSQL tests before the next group is migrated.

### Stage E — operational tools and CI
1. Keep the SQLite backup tool explicitly SQLite-only and prevent it from being used against PostgreSQL.
2. Add PostgreSQL backup/export and restore tooling using supported PostgreSQL utilities or a tested driver-level export strategy.
3. Add PostgreSQL 17 as a disposable GitHub Actions service with health checks and synthetic credentials.
4. Run the full SQLite suite and PostgreSQL integration suite on every PR affecting the database layer.
5. Add migration-upgrade, tenant-isolation, rollback, concurrent-write and restore tests.

**Acceptance:** both database test suites are green in CI; no test requires production or the Neon staging database.

### Stage F — isolated staging verification
1. Only after Stages A–E pass, connect the already-approved free Neon staging project to the isolated Render staging service.
2. Use fictional schools, staff, students, grades and payments only.
3. Verify login and every critical authenticated role workflow on desktop and mobile.
4. Verify data survives process restart and a staging redeploy.
5. Perform a backup, restore into a separate staging target, and compare record counts and integrity.
6. Rehearse rollback to the previous staging build and document exact recovery steps.

**Acceptance:** authenticated workflows, persistence, backup/restore and rollback evidence are recorded. Production remains untouched pending a separate release decision.

## 5. Required automated test matrix

| Area | SQLite | PostgreSQL |
|---|---|---|
| Fresh schema and repeated initialization | Required | Required |
| Supported legacy schema upgrades | Required | Required |
| Migration rollback and unknown ledger rejection | Required | Required |
| Row mapping and parameter binding | Required | Required |
| Foreign keys and school ownership | Required | Required |
| Role authorization and cross-school object access | Required | Required |
| Audit event atomicity | Required | Required |
| Payment and admission-number concurrency | SQLite-specific behavior documented | Real concurrent sessions required |
| Savepoints and caller-owned transactions | Required | Required |
| Backup, restore and integrity | SQLite backup API | PostgreSQL dump/restore |
| Full existing regression suite | Required | Equivalent behavior coverage required |

## 6. Current verification and release boundary

The latest connection-factory candidate is commit `771a15ec01fa6d5850c35b83fe336f1760a5ff6c`. GitHub Actions run [#722](https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45928040387) completed successfully: **190 tests passed on Python 3.11 and 190 passed on Python 3.14**.

This is SQLite regression evidence only. It does not establish PostgreSQL connectivity, PostgreSQL schema compatibility, data migration correctness or staging durability.

The PR remains draft and unmerged. The Neon staging schema remains empty and untouched; no staging `DATABASE_URL` has been configured; no production service, production database or school records have been changed. Do not merge this workstream into `main`, attach it to production, or begin Version 5 until all required Version 4 release gates are satisfied.
