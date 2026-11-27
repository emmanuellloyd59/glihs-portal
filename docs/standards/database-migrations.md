# Database and Migration Standards

## Current baseline

The inspected application uses SQLite, startup initialization/migration logic and parameterized SQL. PostgreSQL compatibility work must be treated as a separate verified workstream. These standards do not declare PostgreSQL ready or authorize a production database switch.

## Migration principles

- Prefer additive, idempotent migrations: create new tables/columns/indexes and backfill in controlled batches.
- Never drop, rename or reinterpret a populated column in the same step that introduces its replacement.
- Preserve existing primary keys, password hashes, student records, guardian links, academic history, finance values and audit history.
- Do not invent historical payment transactions from aggregate balances.
- Do not run destructive migrations automatically on application startup.
- Every migration has a stable version/identifier, purpose, preconditions, postconditions, transaction behavior, estimated lock/downtime impact, rollback/recovery approach and test evidence.
- Migration execution must be serialized; do not allow concurrent web workers to race schema changes.
- Make repeated execution safe where the migration framework supports it; otherwise fail clearly when already applied.
- Parameterize all data values and validate legacy data before adding stricter constraints.

## Required migration workflow

1. Inventory current schema, indexes, constraints, triggers, startup migrations and affected query paths.
2. Capture a verified backup and document its restore procedure before touching valuable data.
3. Test against disposable copies representing current and legacy schema states.
4. Record pre-migration counts, key relationships and financial aggregates.
5. Apply the additive migration in a transaction where supported.
6. Validate IDs, row counts, uniqueness, foreign keys, nullability, tenant ownership and business totals.
7. Run focused migration tests, full regression tests and tenant-isolation tests.
8. Rehearse rollback or forward-recovery against a disposable database.
9. Obtain explicit approval before production execution.
10. Record migration version, operator, timestamp, result and recovery reference without logging sensitive data.

## SQLite requirements

- Preserve SQLite-compatible SQL and connection behavior while SQLite remains supported.
- Enable and verify foreign-key enforcement on every connection.
- Test transaction and locking behavior; do not assume DDL rollback semantics without verifying the actual operations.
- Avoid relying on permissive SQLite typing for correctness; validate dates, amounts, status values and identifiers.
- Never copy a live database file while writes are in progress without a consistent backup procedure.

## Future PostgreSQL compatibility

A PostgreSQL transition requires its own approved ADR and compatibility plan covering driver/adapter behavior, placeholders, transaction semantics, generated IDs, conflict handling, catalog introspection, data types, migrations, pooling, RLS and integration tests. Validate both database engines only if dual-engine support is an explicit maintained requirement; otherwise document the cutover and rollback strategy.

For tenant isolation, application authorization and database constraints/RLS are complementary. RLS is not considered active until policies, connection context, pooling safety and tests under the ordinary application role are proven.

## Rollback and recovery

For additive schema changes, prefer application rollback plus a forward-compatible schema where possible. If data transformations are involved, provide a tested reverse transformation or restore/reconciliation plan. Do not claim rollback safety merely because a migration has a `down` function. Production restore requires explicit authorization and reconciliation of writes made after the backup.
