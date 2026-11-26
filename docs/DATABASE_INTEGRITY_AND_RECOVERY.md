# CMHS Digital Campus — Database Integrity and Recovery

## Scope and safety

All migration, failure-injection, backup and restore tests use temporary SQLite databases with fictional records. No live school database is reset, copied, restored over, or otherwise modified by these tests. Restore helpers always create a new destination and refuse to overwrite an existing file.

## Migration safety

- `initialize_database()` now executes schema and data changes inside an explicit `BEGIN IMMEDIATE` transaction.
- On any migration exception, the transaction is rolled back and the connection restores foreign-key enforcement before closing.
- The schema setup remains idempotent and records the baseline and tenant-ownership guard versions in `schema_migrations`.
- Startup verifies `PRAGMA foreign_key_check` and audits existing student-account, parent-child, finance, grade, attendance, report-card, teacher-assignment and timetable school ownership before committing. A broken relationship blocks the migration rather than silently accepting corruption.
- Existing student IDs, user IDs, grade IDs and legacy records are retained by the existing table-copy migrations; the regression suite exercises a legacy upgrade and repeat initialization.

## School relationship integrity

Database triggers now reject inconsistent school ownership for:
- Student-to-account links.
- Parent-to-student links.
- Student finance rows.
- Grades, attendance and report cards.
- Teacher assignments and timetable entries.
- Finance payment rows, including the receiving user.
- Learning-material uploads and announcement authors/editors.
- Attempts to reassign a linked student or parent/teacher account to another school while dependent records still exist.

Application route checks remain required. These triggers are a second integrity boundary, not a substitute for authorization and tenant-scoped queries. Existing rows should still be audited before any production migration.

## Backup and restore helpers

`scripts/sqlite_backup.py` provides:
- `copy_database(source, destination)`: uses SQLite's online backup API, publishes atomically, and validates integrity and foreign keys.
- `copy_database(backup, restore_destination)`: verifies the source backup and restores to a new file only (without `--overwrite`).

The script verifies `PRAGMA integrity_check` and `PRAGMA foreign_key_check`, refuses same-path operations and existing destinations by default, and cleans up incomplete temporary output.

These are tested helper functions, not a configured production backup scheduler or an operational disaster-recovery service. Production backup retention, off-host copies, encryption/key management, restore-point objectives and scheduled restore drills remain separate operational work.

## Automated verification

The disposable-database tests cover:
- Legacy record preservation during upgrade.
- Migration rollback when a legacy record cannot be copied into the new schema.
- Repeatable initialization and migration-ledger idempotency.
- Preservation of parent-child relationships.
- Rejection of cross-school parent, student-account, finance and finance-payment relationships.
- SQLite integrity and foreign-key checks after restore.
- Refusal to overwrite existing backup or restore files.
- Rejection and cleanup of backups containing foreign-key violations.

The tests must pass in the repository's Python 3.11 and 3.14 CI jobs before Prompt 03 can be marked verified.

## Known limitations

- The application still uses a consolidated startup schema-upgrade function rather than a fully separated migration file per historical schema change. The new ledger records the baseline and integrity-guard milestones; it does not retroactively split every old schema operation into individual migration scripts.
- The backup helpers are not yet connected to a scheduled production backup service.
- Production restore drills, backup encryption, off-host retention and verified persistent storage are not configured.
- Any production migration requires a separately approved backup and recovery plan. The currently documented Render service has no persistent disk or connected PostgreSQL database, so SQLite persistence remains a release blocker.
