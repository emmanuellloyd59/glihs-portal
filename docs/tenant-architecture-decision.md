# Architecture decision: shared database with enforced school ownership

- Status: Accepted for implementation planning; application implementation is still incomplete.
- Date: 2026-10-19
- Scope: CMHS Digital Campus / GLIHS Portal multi-school SaaS evolution.

## Decision

Use one relational database with a `schools` registry and explicit school ownership on every school-owned record. Existing global login identity and platform operator authority must be separated from school membership. A user may only act within schools for which the database grants membership.

This is preferred for the current Flask/SQLite codebase because it avoids introducing a separate database selection and connection lifecycle into every request. SQLite can support a small pilot, but a managed production database may be required as the number of schools and concurrent users grows.

## Non-negotiable safeguards

1. Resolve active school membership from authenticated server-side database state. Never trust a submitted `school_id`, URL parameter, hidden input, or cookie as proof of authorization.
2. A school-owned record must have a non-null school identifier after migration.
3. Scope every list, search, detail, count, dashboard, export, report, create, update, and delete operation.
4. Scope joins and relationships as well as primary table queries. Enforce same-school relationships with composite foreign keys where practical.
5. Distinguish platform operators from school administrators. School administrators cannot create platform operators or elevate their own role.
6. Audit cross-school administrative operations; default-deny any operation that is not explicitly authorized.
7. Keep existing records in a designated legacy school during migration. Preserve primary keys and all history.
8. Do not deploy until migration and restore have been tested on a copy, all tests pass, and browser/security review is complete.

## Initial data model

- `schools`: immutable ID, unique slug, display name, active/suspended status, created timestamp.
- `school_memberships`: user ID, school ID, school-scoped role, status, created timestamp; unique user/school pair.
- `users`: authentication identity. Platform-level privileges are not granted through a school membership role.
- School-owned entities: students, grades, attendance, report cards, student finances, and school-scoped audit events must be associated with a school. Staff/student/guardian links must be validated against the same school.
- Where an existing entity is reached through a student, the code must still validate the student's school in the same transaction. Adding a school column is not enough if routes can use a cross-school student ID.

## Migration plan

1. Make a byte-for-byte backup of the database and test on a disposable copy.
2. Create the registry and membership tables.
3. Create one explicitly designated legacy school; map existing records and existing users to it without changing IDs or password hashes.
4. Add and backfill school ownership; validate that no school-owned row is unmapped.
5. Add required NOT NULL, unique, and relationship constraints after the backfill has been validated.
6. Verify counts and representative relationships before and after migration.
7. Run the migration twice to prove repeatability, then restore the backup to a separate database and verify the restored app can read the records.
8. Do not run this migration against production until the owner explicitly approves.

## Required two-school adversarial tests

Create fictional School A and School B, with separate admins, teachers, students, and records. Test cross-school access by changing IDs in URLs and POST forms, searching, direct navigation, report publication, grade/attendance writes, finance updates, account linking, and school administrator user management. Requests must be denied or return a non-enumerating not-found response, and no data may change.

Also test suspended memberships, invalid school membership, database-level cross-school relationships, legacy migration preservation, repeatability, backup/restore, and all existing regression tests.

## Current implementation status

This document records the architecture choice only. The current Flask application has not yet been converted end-to-end to this model. Multi-school support is not safe to use until the code changes and verification above are complete.
