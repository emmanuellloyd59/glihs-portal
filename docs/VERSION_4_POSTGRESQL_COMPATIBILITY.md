# Version 4 PostgreSQL Compatibility Workstream

Status: **OPEN — implementation not yet complete**

Target: the isolated `feature/v4-postgresql-compatibility` branch, based on
`version-4/reconciled-main-2026-11-26`. This workstream must not be merged
into `main` or deployed to the production Render service without a separate
release decision.

## Verified starting state

- The Version 4 application currently imports and uses Python's `sqlite3`
  directly.
- The database path is configured through `DATABASE_PATH`; the application
  does not consume `DATABASE_URL`.
- The current application contains approximately 220 `.execute()` call
  sites, 389 SQLite-style positional placeholders, SQLite-specific
  `PRAGMA` and `sqlite_master` queries, SQLite exception handling, and
  `lastrowid` / `sqlite3.Row` assumptions.
- The existing migration and integrity safeguards are SQLite-specific.
- A separate Neon PostgreSQL 17 staging project and branch are available.
  No school records have been copied into it.

## Safety constraints

1. Preserve the existing SQLite backend and all current SQLite tests.
2. Never point this work at production credentials or production data.
3. Use synthetic school data and isolated staging credentials only.
4. Do not silently treat `DATABASE_URL` as supported until a PostgreSQL
   backend is actually implemented and tested.
5. Do not weaken tenant isolation, foreign-key enforcement, migration
   atomicity, audit behavior, or backup/restore protections to make a test pass.
6. Do not merge or deploy this workstream to production as part of the
   compatibility implementation.

## Required implementation sequence

1. Introduce a database interface with explicit SQLite and PostgreSQL
   backends. Backend selection must be explicit and fail closed on unsupported
   or malformed configuration.
2. Port schema creation and migrations using backend-specific, transactional
   migration implementations. Preserve the migration ledger and validate
   ownership/foreign-key invariants before recording migration completion.
3. Adapt row handling, parameter binding, generated IDs, integrity exceptions,
   transaction/savepoint behavior, and SQL dialect differences.
4. Port all query paths, including authorization-sensitive joins and
   school-scoped report/finance/homework operations.
5. Add PostgreSQL-specific integration tests for fresh initialization,
   upgrades, rollback, concurrent writes, tenant isolation, and backup/export
   recovery. Keep the full SQLite suite green.
6. Deploy only to the isolated staging service, configure its Neon connection
   as a secret, then test fictional authenticated users and records.
7. Verify persistence across process restart/redeploy, and rehearse backup,
   restore, and rollback before considering a release.

## Release gate

PostgreSQL compatibility is not complete until both backend test suites pass
and authenticated staging workflows, persistence, backup/restore, and
rollback are demonstrated. The existing Version 4 PR remains unmerged while
these gates are open.

## Verification update — 2026-11-27

The first CI run for this workstream completed successfully:

- Workflow run: [Automated tests #719](https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45926778774)
- Python 3.11: **188 tests passed**
- Python 3.14: **188 tests passed**
- The new fail-closed DATABASE_URL regression test is included in both runs.

This verifies the current SQLite application regression suite and the guard against silently ignoring a PostgreSQL URL. It does **not** verify PostgreSQL connectivity, schema creation, migrations, authenticated workflows, or persistent staging data.

### Confirmed implementation inventory

A fresh inspection of the current app.py on this branch found 3,329 lines and these database-specific touchpoints:

- 220 direct SQL .execute() call sites.
- 389 SQLite-style positional placeholders.
- 15 SQLite references, including direct connection creation and SQLite error types.
- 15 PRAGMA references and 2 sqlite_master catalog queries.
- 3 lastrowid assumptions.
- Explicit SQLite transaction/savepoint behavior and SQLite-specific migration logic.

This is why a superficial connection-string or driver swap would be unsafe. The compatibility implementation must cover the entire query and migration surface, not merely establish a TCP connection.

### Current release decision

The fail-closed guard is verified. PostgreSQL compatibility remains **not implemented**. The Neon staging database remains empty and untouched; no Render environment variable has been set, no student records have been migrated, and no production service or database has been changed. Keep this PR in draft until the PostgreSQL implementation and integration/recovery gates are genuinely complete.


## Expanded backend architecture and source audit — 2026-11-27

The detailed architecture decision, source inventory, staged implementation sequence and dual-backend test matrix are now recorded in [VERSION_4_DATABASE_BACKEND_DESIGN.md](VERSION_4_DATABASE_BACKEND_DESIGN.md).

A source scan of the current application and operational scripts found 291 `.execute()` calls, 3 `.executemany()` calls and 537 raw question-mark tokens across the inspected production files. The question-mark count is a static token count, not a claim that every occurrence is a bind marker; any conversion must use a SQL-aware scanner and tests. Additional findings include 22 PRAGMA occurrences, 4 sqlite_master references, 9 BEGIN IMMEDIATE statements, 5 SAVEPOINT occurrences, 15 SQLite trigger definitions, 19 AUTOINCREMENT declarations, 6 INSERT OR IGNORE statements, 3 lastrowid assumptions and SQLite-specific exception handling.

### Architecture decision

The implementation will retain SQLite and add PostgreSQL as an explicit second backend using Psycopg 3 behind a small DB-API connection/row abstraction. We will not perform a wholesale ORM conversion in this stabilization pass. Backend-specific schema migrations, catalog queries and trigger definitions will remain explicit; no global SQL string replacement will be used. PostgreSQL transaction locking, generated IDs, null-safe comparisons, case-insensitive uniqueness and conflict handling will be implemented according to PostgreSQL semantics and covered by integration tests.

### Latest CI result

The connection-factory candidate at head `771a15ec01fa6d5850c35b83fe336f1760a5ff6c` passed GitHub Actions run [#722](https://github.com/emmanuellloyd59/glihs-portal/actions/runs/45928040387): **190 tests passed on Python 3.11 and 190 passed on Python 3.14**. These are SQLite regression tests only. PostgreSQL is not yet implemented or enabled.

### Next implementation sequence

1. Formalize and test backend configuration, connection, row and exception contracts while preserving SQLite.
2. Add Psycopg 3 connection/transaction primitives and a disposable PostgreSQL 17 CI service.
3. Extract the existing SQLite migrations without behavior changes and implement PostgreSQL-native fresh-install and upgrade migrations.
4. Convert query groups and database-specific semantics with dual-backend regression coverage.
5. Add PostgreSQL backup/restore tooling and integration tests.
6. Only after CI passes, connect the approved Neon project to isolated staging and verify fictional authenticated workflows, persistence, backup/restore and rollback.

The PR remains draft. No Neon schema, staging database URL, production service, production database or school records have been changed.
