# Version 0.3 — Multi-school isolation and release gates

## Status

**Implementation in progress. Not production-ready.**

The first schema-foundation change is now in `app.py`: an idempotent `schools` table and additive `school_id` columns/indexes for users, students, grades, attendance, report cards, fees, and audit events; existing non-super-admin users and legacy school records are assigned to a seeded legacy school. User objects now expose server-loaded `school_id`, and audit events inherit the actor's school.

**Critical limitation:** the application routes and SQL queries have not yet all been tenant-scoped, the new columns are currently nullable for migration compatibility, and no passing CI run has been confirmed. This foundation is not proof of isolation. Do not onboard a second school, store real student records, merge, or deploy this branch until the remaining authorization, migration, and regression gates pass.

## Version 0.3 goals

1. Enforce school ownership at the data-access boundary for all school-owned records.
2. Preserve the existing single-school installation and its data through a tested, reversible migration.
3. Keep platform-wide super-administration separate from school-level roles.
4. Add cross-school authorization tests using two fictional schools.
5. Complete regression, migration, backup/restore, and browser checks before release.

## Architecture decision

Use a shared relational database with explicit tenant ownership, provided every tenant-owned read and write is scoped and verified. Do not rely on hidden navigation links, role names, or client-submitted school IDs for authorization.

- Create a `schools` table with stable primary keys and active/suspended status.
- Add a non-null `school_id` foreign key to school-owned users, students, grades, attendance, report cards, fees, and school audit events.
- Assign existing single-school data to one seeded legacy school during migration. Preserve primary keys, password hashes, and record relationships.
- Keep platform super-admin accounts platform-scoped; school staff accounts must belong to exactly one school.
- Derive the active school from the authenticated server-side user/session, never from an untrusted form field or query parameter.
- Enforce school ownership in the database query and in the referenced-record validation for every operation. A student ID from another school must be treated as not found/unauthorized.
- Replace globally unique school-local identifiers (such as admission numbers) with uniqueness scoped to the school, using a database constraint such as `UNIQUE(school_id, admission_number)`. Preserve global uniqueness only where intentional, such as a username if that is the chosen login policy.
- Use parameterized SQL and explicit transaction boundaries. Never concatenate a client-provided school identifier into SQL.
- Add migration backups and a documented rollback/recovery procedure before running a migration against production data.

## Required route and query inventory

Audit every query and mutation for these areas, including dashboard counts, search, joins, exports, and reports:

- Student registration, listing, search, update, and status changes
- Grade creation, listing, editing, and duplicate checks
- Attendance creation, listing, and duplicate checks
- Report-card build, publish/unpublish, and student self-service
- Fee/finance reads and writes
- Parent/student account-to-student linking
- Staff/user creation, approval, suspension, and role changes
- Audit logs, notifications, timetable, and future modules

Every join between tenant-owned records must verify compatible school ownership; filtering only the first table is not sufficient protection.

## Mandatory automated acceptance tests

Create two schools, School A and School B, with separate administrators, teachers, students, grades, attendance, report cards, and fee records.

- School A staff can read and mutate only School A records.
- School B staff can read and mutate only School B records.
- Guessing another school's student ID, grade ID, report-card ID, or finance ID never reveals or changes that record.
- A cross-school student ID submitted when recording grades or attendance is rejected and writes no data.
- Search, dashboard counts, joins, report-card generation, and student self-service never leak another school's data.
- School-local admission numbers can repeat in different schools but cannot duplicate within one school.
- A school staff member cannot create a platform super-admin or change their own school ownership.
- A suspended school or user cannot continue accessing protected data.
- The legacy migration preserves record counts and relationships; rerunning it is safe and does not duplicate data.
- Backup restoration is tested against a disposable copy before production use.

## Release gates

- [ ] All application routes and SQL queries audited for tenant scoping
- [ ] Schema migration tested against a copy of a legacy database
- [ ] Two-school negative-access test suite passes
- [ ] Full test suite passes on supported Python versions
- [ ] Browser checks pass on desktop and mobile
- [ ] Report cards verified in print/PDF preview
- [ ] Backup restore and rollback rehearsal succeeds
- [ ] Code review completed and pull request approved
- [ ] Staging deployment verified before production deployment

## Scope control

Version 0.3 must first deliver secure multi-school foundations. Parent portals, staff workflows, timetables, notifications, and additional finance features must not be advertised as production-ready until each module has server-side permissions, tenant-scoped queries, and automated tests.

**Do not merge or deploy this planning document as proof that tenant isolation is implemented.**