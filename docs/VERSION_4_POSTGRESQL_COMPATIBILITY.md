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
